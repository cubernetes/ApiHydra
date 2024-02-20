import os
import sys
import json
import time
import copy
import atexit
import resource
import threading
from datetime import datetime
from typing import Any, TextIO
from collections import defaultdict
from abc import ABC, abstractmethod
import logging
from logging import DEBUG, INFO, WARNING, ERROR, FATAL

import requests


DEFAULT_APPS_FILE = './apps.json'
DEFAULT_RESPONSES_FILE = './.responses_%s_%s.json' # %s -> current timestamp
TMPFS_FALLBACK_FILE = '/tmp/.responses_%s_%s.json' # %s -> current timestamp
# if this file exists, then program will try to finish gracefully and serialize
# what needs to be serialized
EMERGENCY_STOP_FILE = './SHUTDOWN_HYDRA'

class ApiHydra(ABC):
    """Abstract Base Class for other ApiHydra subclasses. This class implements
       the client side request load balancing (rotating app tokens), statistics,
       threading, credential serialization, and logging.
    """

    def __init__(
        self,
        *,
        api_base: str,
        log_level: int=INFO,
        log_file: TextIO=sys.stdout,
        stats: bool=False,
        max_retries: int=50,
        min_request_delay: float=0.01,
        requests_per_second: float=1/4,
        retry_delay_factor: float=1.1,
        apps_file: str=DEFAULT_APPS_FILE,
        responses_file_path_template: str=DEFAULT_RESPONSES_FILE,
        response_serialization_part: int=1,
    ) -> None:
        self.ensure_resource_limits()
        self.api_base = api_base
        self.log_level = log_level
        self.log_file = log_file
        self.stats = stats
        self.max_retries = max_retries
        self.min_request_delay = min_request_delay
        self.requests_per_second = requests_per_second
        self.retry_delay_factor = retry_delay_factor
        self.apps_file = apps_file
        self.responses_file_path_template = responses_file_path_template
        self.apps = defaultdict(dict)
        self.app_idx = 0
        self.responses = []
        self.threads = []
        self.thread_counter = 0
        self.deserialize(self.apps_file)
        self.number_of_retries = 0
        self.number_of_ok_requests = 0
        self.number_of_non_ok_requests = 0
        self.response_bytes = 0
        self.serialize_responses_flag = True
        self.del_was_called = False
        self.refresh_tokens_flag = False
        self.response_serialization_part = response_serialization_part
        atexit.register(self.__del__)

    def __del__(self) -> None:
        """Destructor for serialization and statistics logging.
        """
        if self.del_was_called:
            return
        self.serialize(self.apps_file)
        if self.serialize_responses_flag:
            self.serialize_responses(responses_file_path_template=self.responses_file_path_template)
        stats_log_level = INFO if self.stats else DEBUG
        self.log(f'Statistics:', stats_log_level)
        self.log(f'- {self.number_of_ok_requests + self.number_of_non_ok_requests} requests', stats_log_level)
        self.log(f'---- {self.number_of_retries} automatic retries', stats_log_level)
        self.log(f'---- {self.number_of_ok_requests} successful (OK) requests', stats_log_level)
        self.log(f'---- {self.number_of_non_ok_requests} unsuccessful requests', stats_log_level)
        self.log(f'- {self.response_bytes} ({self.response_bytes / 1e6:.2f} MB) bytes received (only OK requests)', stats_log_level)
        self.del_was_called = True

    def finish(self) -> None:
        """Set flags s.t. no auto-serialization will be done.
        """
        self.serialize_responses_flag = False

    def ensure_resource_limits(self):
        """Increase number of open file descriptors (often 1024) to 50K.
        """
        soft_limit_fds, hard_limit_fds = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (50000, hard_limit_fds))

    def deserialize(self, apps_file: str=DEFAULT_APPS_FILE):
        """Basic JSON deserializer for the app uid, secret, and token.
        """
        self.log(f'Deserializing app credentials from "{apps_file}".', DEBUG)
        try:
            with open(apps_file, 'r', encoding='utf-8') as apps_file_reader:
                try:
                    self.apps = defaultdict(dict, json.load(apps_file_reader))
                except json.decoder.JSONDecodeError as exc:
                    self.log(f'Could not deserialize from "{apps_file}" ({exc}).', ERROR)
        except IOError as exc:
            self.log(f'Could not open "{apps_file}" for reading ({exc}).', WARNING)

    def serialize(self, apps_file: str=DEFAULT_APPS_FILE):
        """Basic JSON serializer for the app uid, secret, and token.
        """
        self.log(f'Serializing app credentials to "{apps_file}".', WARNING)
        try:
            with open(apps_file, 'w', encoding='utf-8') as apps_file_writer:
                try:
                    json.dump(self.apps, apps_file_writer, indent=4, ensure_ascii=False, sort_keys=True)
                except TypeError as exc:
                    self.log(f'Could not serialize self.apps ({exc}).', FATAL)
                    self.log(f'Dump of self.apps:', FATAL)
                    self.log(f'{self.apps}', FATAL)
        except IOError as exc:
            self.log(f'Could not open "{apps_file}" for writing ({exc}).', FATAL)
            self.log(f'Dump of self.apps:', FATAL)
            self.log(f'{self.apps}', FATAL)

    def serialize_responses(self, responses: list=[], responses_file_path_template: str=DEFAULT_RESPONSES_FILE, part: int=0):
        """Raw serializer for the response object, so hopefully nothing is lost.
        """
        if not responses:
            responses = self.responses
        serializable_responses = []
        for resp in responses:
            serializable_responses.append((resp[0], resp[1].content.decode('utf-8', errors='backslashreplace')))
        try:
            file_name = responses_file_path_template % (part, int(time.time()))
        except TypeError:
            file_name = f'./.responses_{time.time()}.json'
        try:
            with open(file_name, 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                json.dump(serializable_responses, responses_file_writer, indent=4, ensure_ascii=False)
                self.log(f'Serialized responses to "{file_name}".', WARNING)
        except TypeError as exc:
            self.log(f'Could not serialize responses ({exc}).', FATAL)
            self.log(f'Trying str repr instead', FATAL)
            with open(file_name + '.py', 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                responses_file_writer.write(str(serializable_responses))
                self.log(f'Serialized responses to "{file_name}".', INFO)
        except IOError as exc:
            self.log(f'Could not open "{file_name}" for writing ({exc}), trying file on tmpfs.', FATAL)
            try:
                file_name = TMPFS_FALLBACK_FILE % (part, int(time.time()))
            except TypeError:
                file_name = f'./.responses_{time.time()}.json'
            try:
                with open(file_name, 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                    json.dump(self.apps, responses_file_writer, indent=4, ensure_ascii=False)
                    self.log(f'Serialized responses to "{file_name}".', INFO)
            except TypeError as exc:
                self.log(f'Could not serialize responses ({exc}).', FATAL)
                self.log(f'Trying str repr instead', FATAL)
                with open(file_name + '.py', 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                    responses_file_writer.write(str(serializable_responses))
                    self.log(f'Serialized responses to "{file_name}".', INFO)
            except IOError as exc:
                self.log(f'Could not open "{TMPFS_FALLBACK_FILE}" for writing ({exc}), sorry, all response data is now lost.', FATAL)
        except Exception as exc:
            self.log(f'Unhandled exception: {exc}. Sorry, all response data is now lost', FATAL)

    def log(self, msg: str, log_level: int=INFO, end='\n'):
        """Simpler logger. Don't apply color if file is not a terminal.
           No buffering is done.
        """
        if log_level < self.log_level:
            return;
        clr_rst = '\033\x5bm'
        if not self.log_file.isatty():
            clr_rst = ''
            ansi = ''
        if log_level == DEBUG:
            ansi = '\033\x5b36m' # grey
        elif log_level == INFO:
            ansi = '\033\x5b0m' # Default/white
        elif log_level == WARNING:
            ansi = '\033\x5b33m' # Yellow
        elif log_level == ERROR:
            ansi = '\033\x5b31m' # Red
        elif log_level == FATAL:
            ansi = '\033\x5b41;30m' # Inverted Red
        else:
            clr_rst = ''
            ansi = ''
        print(f'{ansi}[{str(datetime.now())}, {self.__class__.__name__}, {logging.getLevelName(log_level):>10}] {msg}{clr_rst}', end=end, file=self.log_file, flush=True)

    def get_next_app(self):
        """Rotate through the list of available apps uniformily across
           the lifetime of the instance. Uniformity breaks if many instances
           are created.
        """
        app_id = list(self.apps)[self.app_idx % len(self.apps)]
        self.app_idx += 1
        app = self.apps[app_id]
        return app

    @abstractmethod
    def make_request_kwargs_from_app(self, app: dict, **kwargs) -> dict[str, Any]:
        """Abstract method that must be implemented by every derived class.
           No request will be possible if this is not implemented.
       """
        pass

    def _get(self, *args, **kwargs) -> None:
        """Threaded method that is wrapped by self.get.
           This shall be a transparent method, do not call it directly.
           Makes a simple get request.
        """
        if not args and 'url' not in kwargs:
            self.log(f'{threading.current_thread().name}: Call to {self.__class__.__name__}._get requires at least url argument (either as 1st arg or as kwarg).', ERROR)
            return
        elif 'url' in kwargs:
            url = kwargs['url']
            if url.startswith('/'):
                url = self.api_base + url
                kwargs['url'] = url
        else:
            url = args[0]
            if url.startswith('/'):
                url = self.api_base + url
                args = list(args)
                args[0] = url
        retries = 0
        delay = 1 / len(self.apps)
        while True:
            if retries > self.max_retries:
                self.log(f'{threading.current_thread().name}: Data loss: get request to "{url}" and "{args=}", "{kwargs=}" exceeded max retries ({self.max_retries}).', ERROR)
                return
            elif retries != 0:
                self.number_of_retries += 1
            app = self.get_next_app()
            kwargs = self.make_request_kwargs_from_app(app, **kwargs)
            resp = requests.get(*args, **kwargs)
            if resp.status_code != 200:
                self.number_of_non_ok_requests += 1
                failed_req_log_level = DEBUG if resp.status_code == 429 else WARNING
                self.log(f'{threading.current_thread().name}: Could not get "{url}" ({resp.status_code}) (retries: {retries}).', failed_req_log_level)
                if retries > 5 and resp.status_code == 404:
                    self.log(f'{threading.current_thread().name}: After {retries} gets on "{url}": 404 not found. Returning early.', failed_req_log_level)
                    return
                time.sleep(delay)
                delay *= self.retry_delay_factor
            else:
                self.number_of_ok_requests += 1
                self.log(f'{threading.current_thread().name}: Successful get: "{url}".', DEBUG)
                self.response_bytes += len(resp.content)
                self.responses.append((url, resp))
                return
            retries += 1

    def _post(self, *args, **kwargs) -> None:
        """Threaded method that is wrapped by self.post.
           This shall be a transparent method, do not call it directly.
           Makes a simple post request.
        """
        if not args and 'url' not in kwargs:
            self.log(f'{threading.current_thread().name}: Call to {self.__class__.__name__}._post requires at least url argument (either as 1st arg or as kwarg).', ERROR)
            return
        elif 'url' in kwargs:
            url = kwargs['url']
            if url.startswith('/'):
                url = self.api_base + url
                kwargs['url'] = url
        else:
            url = args[0]
            if url.startswith('/'):
                url = self.api_base + url
                args = list(args)
                args[0] = url
        retries = 0
        delay = 1 / len(self.apps)
        while True:
            if retries > self.max_retries:
                self.log(f'{threading.current_thread().name}: Data loss: post request to "{url}" and "{args=}", "{kwargs=}" exceeded max retries ({self.max_retries}).', ERROR)
                return
            elif retries != 0:
                self.number_of_retries += 1
            app = self.get_next_app()
            kwargs = self.make_request_kwargs_from_app(app, **kwargs)
            resp = requests.post(*args, **kwargs)
            if resp.status_code != 200:
                self.number_of_non_ok_requests += 1
                failed_req_log_level = DEBUG if resp.status_code == 429 else WARNING
                self.log(f'{threading.current_thread().name}: Could not post "{url}" ({resp.status_code}) (retries: {retries}).', failed_req_log_level)
                if retries > 5 and resp.status_code == 404:
                    self.log(f'{threading.current_thread().name}: After {retries} posts on "{url}": 404 not found. Returning early.', failed_req_log_level)
                    return
                time.sleep(delay)
                delay *= self.retry_delay_factor
            else:
                self.number_of_ok_requests += 1
                self.log(f'{threading.current_thread().name}: Successful post: "{url}".', DEBUG)
                self.response_bytes += len(resp.content)
                self.responses.append((url, resp))
                return
            retries += 1

    def join(self):
        """Wait for all threads to finish.
        """
        self.log(f'Joining threads...', INFO)
        while True:
            for thread in self.threads:
                thread.join(1 / len(self.threads))
            if all([not thread.is_alive() for thread in self.threads]):
                break
        self.log(f'All threads joined.', INFO)
        self.log(f'Clearing threads list.', DEBUG)
        self.threads.clear()

    def clear_responses(self):
        """Clear the response list.
        """
        self.log(f'Clearing responses list.', INFO)
        self.responses.clear()

    def get_responses(self):
        """Return the list of responses without making a copy.
           Calling self.clear_responses() will clear the list,
           so any reference to this list will also lose the data.
        """
        self.join()
        self.log(f'Returning responses (no copy).', DEBUG)
        return self.responses

    def get_responses_copy(self):
        """Return the list of responses as a deepcopy.
           Calling self.clear_responses() will clear the original list,
           but not the list that is returned by this method.
        """
        self.join()
        self.log(f'Returning responses (deepcopy).', DEBUG)
        return copy.deepcopy(self.responses)

    def ensure_ready(self):
        if os.path.isfile(EMERGENCY_STOP_FILE):
            self.log(f'Main Thread: Found emergency stop file "{EMERGENCY_STOP_FILE}", finishing up.', FATAL)
            self.join()
            raise SystemExit(42)
        if self.refresh_tokens_flag:
            while self.refresh_tokens_flag:
                time.sleep(0.5)
        if threading.active_count() / 2 > len(self.apps):
            self.log(f'Number of active threads ({threading.active_count()}) exceeded double the number of apps available ({len(self.apps)}). Waiting until number of active threads == number of apps.', WARNING)
            while threading.active_count() >= len(self.apps):
                time.sleep(0.5)
            self.log(f'Active thread count == number of available apps == ({len(self.apps)}).', WARNING)
        if len(self.responses) > 10_000:
            self.join()
            self.serialize_responses(responses_file_path_template='./response_part_%s_%s.json', part=self.response_serialization_part)
            self.response_serialization_part += 1
            self.clear_responses()

    def get(self, *args, **kwargs):
        """Construct a list of threads of get requests that will be started immediately,
           but wait specific time before next request can be made to avoid
           rate limiting. Caller can wait for all threads to finish by calling
           the self.join method.
        """
        self.ensure_ready()
        self.log(f'Creating new thread with function {self.__class__.__name__}._get and "{args=}", "{kwargs=}".', DEBUG)
        self.threads.append(
            threading.Thread(target=self._get, daemon=True, name=f'{self.__class__.__name__}._get-Thread-{self.thread_counter}', args=args, kwargs=kwargs)
        )
        self.thread_counter += 1
        self.threads[-1].start()
        delay = (1 / self.requests_per_second) / len(self.apps)
        time.sleep(max(delay, self.min_request_delay))

    def post(self, *args, **kwargs):
        """Construct a list of threads of post requests that will be started immediately,
           but wait specific time before next request can be made to avoid
           rate limiting. Caller can wait for all threads to finish by calling
           the self.join method.
        """
        self.ensure_ready()
        self.log(f'Creating new thread with function {self.__class__.__name__}._post and "{args=}", "{kwargs=}".', DEBUG)
        self.threads.append(
            threading.Thread(target=self._post, daemon=True, name=f'{self.__class__.__name__}._post-Thread-{self.thread_counter}', args=args, kwargs=kwargs)
        )
        self.thread_counter += 1
        self.threads[-1].start()
        delay = (1 / self.requests_per_second) / len(self.apps)
        time.sleep(max(delay, self.min_request_delay))
