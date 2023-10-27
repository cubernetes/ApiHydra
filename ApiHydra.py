#!/usr/bin/env python3

import re
import sys
import json
import time
import copy
import requests
import threading
from datetime import datetime
from typing import Any, TextIO
from collections import defaultdict
from abc import ABC, abstractmethod
import logging
from logging import DEBUG, INFO, WARNING, ERROR, FATAL

from bs4 import BeautifulSoup, Tag


DEFAULT_APPS_FILE = './apps.json'
DEFAULT_RESPONSES_FILE = './.responses_%s.json' # %s -> current timestamp
TMPFS_FALLBACK_FILE = '/tmp/.responses_%s.json' # %s -> current timestamp

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
    ) -> None:
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

    def __del__(self):
        """Destructor for serialization and statistics logging.
        """
        self.serialize(self.apps_file)
        if self.serialize_responses_flag:
            self.serialize_responses(self.responses_file_path_template)
        stats_log_level = INFO if self.stats else DEBUG
        self.log(f'Statistics:', stats_log_level)
        self.log(f'- {self.number_of_ok_requests + self.number_of_non_ok_requests} requests', stats_log_level)
        self.log(f'---- {self.number_of_retries} automatic retries', stats_log_level)
        self.log(f'---- {self.number_of_ok_requests} successful (OK) requests', stats_log_level)
        self.log(f'---- {self.number_of_non_ok_requests} unsuccessful requests', stats_log_level)
        self.log(f'- {self.response_bytes} ({self.response_bytes / 1e6:.2f} MB) bytes received (only OK requests)', stats_log_level)

    def finish(self) -> None:
        """Set flags s.t. no auto-serialization will be done.
        """
        self.serialize_responses_flag = False

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

    def serialize_responses(self, responses_file_path_template: str=DEFAULT_RESPONSES_FILE):
        """Raw serializer for the response object, so hopefully nothing is lost.
        """
        serializable_responses = []
        for resp in self.responses:
            serializable_responses.append((resp[0], resp[1].content.decode('utf-8', errors='backslashreplace')))
        try:
            try:
                file_name = responses_file_path_template % (int(time.time()),)
            except TypeError:
                file_name = f'./.responses_{time.time()}.json'
            try:
                with open(file_name, 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                    json.dump(serializable_responses, responses_file_writer, indent=4, ensure_ascii=False)
                    self.log(f'Serialized responses to "{file_name}".', WARNING)
            except TypeError as exc:
                self.log(f'Could not serialize self.responses ({exc}).', FATAL)
                self.log(f'Trying str repr instead', FATAL)
                with open(file_name + '.py', 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                    responses_file_writer.write(str(serializable_responses))
                    self.log(f'Serialized responses to "{file_name}".', INFO)
        except IOError as exc:
            self.log(f'Could not open "{file_name}" for writing ({exc}), trying file on tmpfs.', FATAL)
            try:
                try:
                    file_name = TMPFS_FALLBACK_FILE % (int(time.time()),)
                except TypeError:
                    file_name = f'./.responses_{time.time()}.json'
                try:
                    with open(file_name, 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                        json.dump(self.apps, responses_file_writer, indent=4, ensure_ascii=False)
                        self.log(f'Serialized responses to "{file_name}".', INFO)
                except TypeError as exc:
                    self.log(f'Could not serialize self.responses ({exc}).', FATAL)
                    self.log(f'Trying str repr instead', FATAL)
                    with open(file_name + '.py', 'w', encoding='utf-8', errors='backslashreplace') as responses_file_writer:
                        responses_file_writer.write(str(serializable_responses))
                        self.log(f'Serialized responses to "{file_name}".', INFO)
            except IOError as exc:
                self.log(f'Could not open "{TMPFS_FALLBACK_FILE}" for writing ({exc}), sorry, all response data is now lost.', FATAL)
        except Exception as exc:
            self.log(f'Unhandled exception: {exc}. Sorry, all response data is now lost', FATAL)

    def log(self, msg: str, log_level: int=INFO):
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
        print(f'{ansi}[{self.__class__.__name__}, {logging.getLevelName(log_level):>10}] {msg}{clr_rst}', file=self.log_file, flush=True)

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
        for thread in self.threads:
            thread.join()
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

    def get(self, *args, **kwargs):
        """Construct a list of threads of get requests that will be started immediately,
           but wait specific time before next request can be made to avoid
           rate limiting. Caller can wait for all threads to finish by calling
           the self.join method.
        """
        self.log(f'Creating new thread with function {self.__class__.__name__}._get and "{args=}", "{kwargs=}".', DEBUG)
        self.threads.append(
            threading.Thread(target=self._get, name=f'{self.__class__.__name__}._get-Thread-{self.thread_counter}', args=args, kwargs=kwargs)
        )
        self.thread_counter += 1
        self.threads[-1].start()
        delay = (1 / self.requests_per_second) / len(self.apps)
        time.sleep(min(delay, self.min_request_delay))

    def post(self, *args, **kwargs):
        """Construct a list of threads of post requests that will be started immediately,
           but wait specific time before next request can be made to avoid
           rate limiting. Caller can wait for all threads to finish by calling
           the self.join method.
        """
        self.log(f'Creating new thread with function {self.__class__.__name__}._post and "{args=}", "{kwargs=}".', DEBUG)
        self.threads.append(
            threading.Thread(target=self._post, name=f'{self.__class__.__name__}._post-Thread-{self.thread_counter}', args=args, kwargs=kwargs)
        )
        self.thread_counter += 1
        self.threads[-1].start()
        delay = (1 / self.requests_per_second) / len(self.apps)
        time.sleep(min(delay, self.min_request_delay))

class FtApiHydra(ApiHydra):
    """ApiHydra subclass that makes interfacing with the 42 intra API not only a piece of cake,
       but also fast, fun, and easy to debug (thanks to logging levels).
    """

    def __init__(
            self,
            *,
            api_base: str='https://api.intra.42.fr/v2',
            log_level: int=INFO,
            log_file: TextIO=sys.stdout,
            stats: bool=False,
            max_retries: int=50,
            requests_per_second: float=1/3,
            min_request_delay: float=0.01,
            retry_delay_factor: float=1.1,
            apps_file: str=DEFAULT_APPS_FILE,
            responses_file_path_template: str=DEFAULT_RESPONSES_FILE,
            intra_login: str,
            intra_password: str,
        ) -> None:
        super().__init__(
            api_base=api_base,
            log_level=log_level,
            log_file=log_file,
            stats=stats,
            max_retries=max_retries,
            min_request_delay=min_request_delay,
            requests_per_second=requests_per_second,
            retry_delay_factor=retry_delay_factor,
            apps_file=apps_file,
            responses_file_path_template=responses_file_path_template
        )
        self.intra_login = intra_login
        self.intra_password = intra_password
        self.session = requests.Session()
        self.is_updated = False

    def ensure_session(self):
        """Checks if self.session has the 'user.id' cookie set,
           if not, creates new session.
        """
        if 'user.id' in self.session.cookies:
            return
        self.create_intra_session(self.intra_login, self.intra_password)

    def requests_get(self, *args, **kwargs) -> requests.models.Response:
        """Wrapper to account for internal statistics
        """
        resp = self.session.get(*args, **kwargs)
        if resp.ok:
            self.number_of_ok_requests += 1
            self.response_bytes += len(resp.content)
        else:
            self.number_of_non_ok_requests += 1
        return resp

    def requests_post(self, *args, **kwargs) -> requests.models.Response:
        """Wrapper to account for internal statistics
        """
        resp = self.session.post(*args, **kwargs)
        if resp.ok:
            self.number_of_ok_requests += 1
            self.response_bytes += len(resp.content)
        else:
            self.number_of_non_ok_requests += 1
        return resp

    def make_request_kwargs_from_app(self, app: dict, **kwargs) -> dict[str, Any]:
        """Intra API implements bearer authentication.
        """
        app_id = app.get('id', '')
        token = app.get('token', '')
        if not app_id:
            self.log(f'Intra app has no id.', ERROR)
        else:
            if not token:
                self.log(f'Intra app "{app_id}" has no token.', ERROR)
                token = f'MISSING_TOKEN_{int(time.time())}'
        kwargs['headers'] = {
            'Authorization': f'Bearer {token}'
        }
        return kwargs

    def create_intra_session(self, intra_login: str, intra_password: str) -> None:
        """Follow and authenticate keycloak flow to create a http session.
           username and password are not logged.
        """
        sign_in_page_url = 'https://profile.intra.42.fr/users/auth/keycloak_student'
        callback_url = 'https://profile.intra.42.fr/users/auth/keycloak_student/callback'
        self.log(f'Creating Intra session...', INFO)
        self.log(f'Getting sign in page ({sign_in_page_url}).', DEBUG)
        r_initial = self.requests_get(sign_in_page_url)
        if r_initial.status_code != 200:
            self.log(f'Could not get sign in page ({r_initial.status_code}) to create session.', ERROR)
            return
        html = r_initial.text
        soup = BeautifulSoup(html, 'html.parser')
        element = soup.find(id='kc-form-login')
        authenticate_url = element.attrs.get('action', '') if isinstance(element, Tag) else ''
        if not authenticate_url:
            self.log(f'Could not extract authentication url (empty) to create session.', ERROR)
            return
        self.log(f'Posting to authentication url ({authenticate_url}).', DEBUG)
        post_creds_resp = self.requests_post(authenticate_url, data={'username': intra_login, 'password': intra_password})
        if post_creds_resp.status_code != 200:
            self.log(f'Could not post credentials ({post_creds_resp.status_code}) to create session.', ERROR)
            return
        self.log(f'Getting callback url ({callback_url}).', DEBUG)
        callback_resp = self.requests_get(callback_url)
        if callback_resp.status_code != 200:
            self.log(f'Could not get callback url ({callback_resp.status_code}) to create session.', ERROR)
        else:
            self.log(f'Intra session creation successful.', INFO)

    def get_token(self, app_id: str, uid: str, secret: str) -> str:
        """Requests an application access token using uid and secret.
        """
        try:
            resp = self.requests_post('https://api.intra.42.fr/oauth/token', data={
                'grant_type': 'client_credentials',
                'client_id': uid,
                'client_secret': secret,
            })
        except KeyboardInterrupt:
            print(end='\n', file=sys.stdout, flush=True)
            self.log(f'Received KeyboardInterrupt.', FATAL)
            raise SystemExit(130)
        if resp.status_code != 200:
            self.log(f'Could not get access token ({resp.status_code}) for app "{app_id}".', ERROR)
            return ''
        else:
            resp = resp.json()
            expires_in = resp['expires_in']
            token = resp['access_token']
            self.log(f'- {token=} {expires_in=}s\n', DEBUG)
            return token

    def update_app(self, app_id: str) -> None:
        """Scrape uid and secret from a given API application from the intra.
        """
        self.ensure_session()
        self.log(f'Getting app credentials (uid, secret) for app "{app_id}".', DEBUG)
        resp = self.requests_get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if resp.status_code != 200:
            self.log(f'Could not get page ({resp.status_code}) to update app "{app_id}".', ERROR)
            return
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')

        secret_div = soup.find('div', {'data-copy': f'[data-app-secret-{app_id}]'}, class_='copy')
        secret = secret_div.get('data-clipboard-text', '') if isinstance(secret_div, Tag) else ''
        uid_div = soup.find('div', {'data-copy': f'[data-app-uid-{app_id}]'}, class_='copy')
        uid = uid_div.get('data-clipboard-text', '') if isinstance(uid_div, Tag) else ''

        secrets = soup.find_all('div', class_='credential')
        if len(secrets) > 2:
            next_secret = soup.find_all('div', class_='credential')[2].text.strip()
            if next_secret == '-':
                next_secret = None
        else:
            next_secret = None

        app_info = soup.find('div', class_='application-info')
        app_name_span = app_info.find('span') if isinstance(app_info, Tag) else None
        app_name = app_name_span.text if isinstance(app_name_span, Tag) else ''

        app_details = list(soup.find_all('div', class_='row'))[2]

        validity_text_divs = soup.find_all('div', class_='rotation-actions')
        current_secret_text = validity_text_divs[0].text
        next_secret_text = validity_text_divs[0].text
        current_secret_expiry_date_match = re.search(r'\d+/\d+/\d+', current_secret_text)
        next_secret_expiry_date_match = re.search(r'\d+/\d+/\d+', next_secret_text)
        if current_secret_expiry_date_match:
            current_secret_expiry_ts = int(datetime.strptime(current_secret_expiry_date_match.group(0), '%d/%m/%Y').timestamp())
        else:
            current_secret_expiry_ts = -1
        if next_secret_expiry_date_match:
            next_secret_expiry_ts = int(datetime.strptime(next_secret_expiry_date_match.group(0), '%d/%m/%Y').timestamp())
        else:
            next_secret_expiry_ts = -1

        h4s = app_details.find_all('h4')
        requests_last_hour = int(h4s[0].next_sibling.next_sibling.find('span').text.strip())
        max_requests_per_hour = int(h4s[0].next_sibling.next_sibling.find_all('span')[1].next_sibling.strip().lstrip('/').strip())
        max_requests_per_second = int(soup.find_all('div', class_='application-desc')[1].find('code').text.strip())
        active_tokens = int(h4s[1].next_sibling.next_sibling.text.strip())
        active_users = int(h4s[2].next_sibling.next_sibling.text.strip())
        total_requests = int(h4s[3].next_sibling.next_sibling.text.strip())
        total_generated_tokens = int(h4s[4].next_sibling.next_sibling.text.strip())
        total_unique_users = int(h4s[5].next_sibling.next_sibling.text.strip())

        header = soup.find('div', class_='header')
        if header:
            redirect_code_block = header.find('code')
            if isinstance(redirect_code_block, Tag):
                redirect_url = redirect_code_block.text.strip()
            else:
                redirect_url = ''
        else:
            redirect_url = ''

        scopes = []
        for node in soup.find_all('label', class_='label-primary'):
            scopes.append(node.text.strip())

        self.apps[app_id]['uid'] = uid
        self.apps[app_id]['secret'] = secret
        self.apps[app_id]['next_secret'] = next_secret
        self.apps[app_id]['app_name'] = app_name
        self.apps[app_id]['requests_last_hour'] = requests_last_hour
        self.apps[app_id]['max_requests_per_hour'] = max_requests_per_hour
        self.apps[app_id]['max_requests_per_seconds'] = max_requests_per_second
        self.apps[app_id]['active_tokens'] = active_tokens
        self.apps[app_id]['active_users'] = active_users
        self.apps[app_id]['total_requests'] = total_requests
        self.apps[app_id]['total_generated_tokens'] = total_generated_tokens
        self.apps[app_id]['total_unique_users'] = total_unique_users
        self.apps[app_id]['current_secret_expiry_ts'] = current_secret_expiry_ts
        self.apps[app_id]['next_secret_expiry_ts'] = next_secret_expiry_ts
        self.apps[app_id]['redirect_url'] = redirect_url
        self.apps[app_id]['scopes'] = scopes
        self.log(f'Successfully scraped credentials for app "{app_id}".', DEBUG)

    def get_app_ids(self) -> list[str]:
        """Scrape user API applications from the intra.
        """
        self.ensure_session()
        self.log(f'Getting app ids.', DEBUG)
        resp = self.requests_get(f'https://profile.intra.42.fr/oauth/applications')
        if resp.status_code != 200:
            self.log(f'Could not get apps overview page ({resp.status_code}).', ERROR)
            return []
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        apps_root = soup.find('div', class_='apps-root')
        apps_data = apps_root.get('data', '[]') if isinstance(apps_root, Tag) else []
        apps_data = json.loads(apps_data) if isinstance(apps_data, str) else []
        app_ids = [str(app_data['id']) for app_data in apps_data if 'id' in app_data]
        return app_ids

    def refresh_tokens(self) -> None:
        """Only refresh the access tokens (fast)
        """
        for i, (app_id, app_creds) in enumerate(self.apps.items()):
            self.log(f'Refreshing token for app "{app_id}" ({i+1}/{len(self.apps)}).', INFO)
            uid = app_creds['uid']
            secret = app_creds['secret']
            token = self.get_token(app_id, uid, secret)
            self.apps[app_id]['token'] = token

    def delete_app(self, app_id: str) -> None:
        """Delete API intra app by id.
        """
        self.ensure_session()
        app_page = self.requests_get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if app_page.status_code != 200:
            self.log(f'Could not get page ({app_page.status_code}) to delete app "{app_id}".', ERROR)
            return
        soup = BeautifulSoup(app_page.text, 'html.parser')
        csrf_token_meta = soup.find('meta', {'name': 'csrf-token'})
        if isinstance(csrf_token_meta, Tag):
            authenticty_token = csrf_token_meta.get('content', '')
            resp = self.requests_post(f'https://profile.intra.42.fr/oauth/applications/{app_id}',
                  data={'_method': 'delete', 'authenticity_token': authenticty_token})
            if resp.status_code != 200:
                self.log(f'Could not delete app "{app_id}" ({resp.status_code}).', ERROR)
            else:
                if app_id in self.apps:
                    del self.apps[app_id]
                self.log(f'Deleted app "{app_id}" successfully.', INFO)
        else:
            self.log(f'Could not delete app "{app_id}", could not find authenticity token.', ERROR)

    def create_app(self, *, update: bool=True):
        """Create new API intra app with prefix "ApiHydra_" followed by index.
        """
        if update and not self.is_updated:
            self.update()
        apps_page = self.requests_get(f'https://profile.intra.42.fr/oauth/applications')
        if apps_page.status_code != 200:
            self.log(f'Could not get apps page ({apps_page.status_code}) to create app.', ERROR)
            return
        soup = BeautifulSoup(apps_page.text, 'html.parser')
        csrf_token_meta = soup.find('meta', {'name': 'csrf-token'})
        if isinstance(csrf_token_meta, Tag):
            authenticty_token = csrf_token_meta.get('content', '')
            resp = self.requests_post(f'https://profile.intra.42.fr/oauth/applications',
                files={
                     'authenticity_token': (None, authenticty_token),
                     'doorkeeper_application[name]': (None, f'ApiHydra_{len(self.apps) + 1}'),
                     'doorkeeper_application[scopes]': (None, ''),
                     'doorkeeper_application[redirect_uri]': (None, 'http://127.0.0.1:80'),
                })
            if resp.status_code == 201:
                app_id = str(resp.json()['id'])
                app_name = resp.json()['name']
                self.apps[app_id]['id'] = app_id
                self.update_app(app_id)
                self.log(f'Created app "{app_name}" ({app_id}).', INFO)
            else:
                self.log(f'Could not create app ({resp.status_code}).', ERROR)
        else:
            self.log(f'Could not create app, could not find authenticity token.', ERROR)

    def get_number_of_apps(self, *, update: bool=True) -> int:
        if update and not self.is_updated:
            self.update()
        return len(self.apps)

    def set_number_of_apps(self, number_of_apps: int, *, update: bool=True) -> None:
        if update and self.is_updated:
            self.update()
        diff = number_of_apps - len(self.apps)
        if number_of_apps < 0:
            self.log(f'Cannot have negative number ({number_of_apps}) of apps.', ERROR)
            return
        elif number_of_apps > 200:
            self.log(f'Cannot have more than 200 ({number_of_apps}) apps.', ERROR)
            return
        elif diff == 0:
            self.log(f'Number of apps is already {number_of_apps}, doing nothing.', DEBUG)
            return
        elif diff < 0:
            self.log(f'Deleting {-diff} apps...', DEBUG)
            for app_id in list(self.apps)[:diff]:
                self.delete_app(app_id)
            self.log(f'Deleted {-diff} apps, new app count is {len(self.apps)}.', INFO)
        else:
            self.log(f'Creating {diff} apps...', DEBUG)
            for _ in range(diff):
                self.create_app()
            self.log(f'Created {diff} apps, new app count is {len(self.apps)}.', INFO)

    def get_requests_left_this_hour(self, *, update: bool=True) -> int:
        if update and self.is_updated:
            self.update()
        requests_left = 0
        for app in self.apps.values():
            requests_left += app['max_requests_per_hour'] - app['requests_last_hour']
        return requests_left

    def update(self) -> None:
        """Fully update the credentials and tokens of all apps that are available
           through the intra.
        """
        self.log(f'Updating Hydra.', DEBUG)
        self.ensure_session()
        app_ids = self.get_app_ids()
        self.log(f'Number of Apps: {len(app_ids)}', DEBUG)
        for i, app_id in enumerate(app_ids):
            self.log(f'Adding app "{app_id}" to apps list ({i+1}/{len(app_ids)}).', INFO)
            self.apps[app_id]['id'] = app_id
            self.update_app(app_id)
        self.refresh_tokens()
        self.is_updated = True
