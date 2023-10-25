#!/usr/bin/env python3

import sys
import json
import time
import copy
import requests
import threading
from typing import Any, TextIO
from collections import defaultdict
from abc import ABC, abstractmethod
import logging
from logging import DEBUG, INFO, WARNING, ERROR, FATAL

from bs4 import BeautifulSoup, Tag


DEFAULT_APPS_FILE = './apps.json'

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
        requests_per_second: float=1/4,
        retry_delay_factor: float=1.1,
        apps_file: str=DEFAULT_APPS_FILE,
    ) -> None:
        self.api_base = api_base
        self.log_level = log_level
        self.log_file = log_file
        self.stats = stats
        self.max_retries = max_retries
        self.requests_per_second = requests_per_second
        self.retry_delay_factor = retry_delay_factor
        self.apps_file = apps_file
        self.apps = defaultdict(dict)
        self.app_idx = 0
        self.responses = []
        self.threads = []
        self.thread_counter = 0
        self.deserialize(self.apps_file)
        self.number_of_caller_requests = 0
        self.number_of_200_requests = 0
        self.number_of_non_200_requests = 0
        self.response_bytes = 0

    def __del__(self):
        self.serialize(self.apps_file)
        stats_log_level = INFO if self.stats else DEBUG
        self.log(f'Statistics:', stats_log_level)
        self.log(f'- {self.number_of_caller_requests} caller requests', stats_log_level)
        self.log(f'- {self.number_of_200_requests + self.number_of_non_200_requests} actual requests', stats_log_level)
        self.log(f'-- {self.number_of_200_requests} successful (200) requests', stats_log_level)
        self.log(f'-- {self.number_of_non_200_requests} unsuccessful requests', stats_log_level)
        self.log(f'- {self.response_bytes} response bytes', stats_log_level)

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
        """
        if not args and 'url' not in kwargs:
            self.log(f'{threading.current_thread().name}: Call to {self.__class__.__name__}._get requires at least url argument (either as 1st arg or as kwarg).', ERROR)
            return;
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
                return;
            app = self.get_next_app()
            kwargs = self.make_request_kwargs_from_app(app, **kwargs)
            resp = requests.get(*args, **kwargs)
            if resp.status_code == 200:
                self.number_of_200_requests += 1
                self.log(f'{threading.current_thread().name}: Successful get: "{url}".', DEBUG)
                self.response_bytes += len(resp.content)
                self.responses.append((url, resp))
                return;
            else:
                self.number_of_non_200_requests += 1
                self.log(f'{threading.current_thread().name}: Could not get "{url}" ({resp.status_code}) (retries: {retries}).', DEBUG)
                time.sleep(delay)
                delay *= self.retry_delay_factor
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
        """Construct a list of threads that will be started immediately,
           but wait specific time before next request can be made to avoid
           rate limiting. Caller can wait for all threads to finish by calling
           the self.join method.
        """
        self.number_of_caller_requests += 1
        self.log(f'Creating new thread with function {self.__class__.__name__}._get and "{args=}", "{kwargs=}".', DEBUG)
        self.threads.append(
            threading.Thread(target=self._get, name=f'{self.__class__.__name__}._get-Thread-{self.thread_counter}', args=args, kwargs=kwargs)
        )
        self.thread_counter += 1
        self.threads[-1].start()
        time.sleep((1 / self.requests_per_second) / len(self.apps))

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
            retry_delay_factor: float=1.1,
            apps_file: str=DEFAULT_APPS_FILE,
            intra_login: str,
            intra_password: str,
        ) -> None:
        super().__init__(
            api_base=api_base,
            log_level=log_level,
            log_file=log_file,
            stats=stats,
            max_retries=max_retries,
            requests_per_second=requests_per_second,
            retry_delay_factor=retry_delay_factor,
            apps_file=apps_file,
        )
        self.intra_login = intra_login
        self.intra_password = intra_password

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

    def create_intra_session(self, intra_login: str, intra_password: str) -> requests.Session:
        """Follow and authenticate keycloak flow to create a http session.
           username and password are not logged.
        """
        sign_in_page_url = 'https://profile.intra.42.fr/users/auth/keycloak_student'
        callback_url = 'https://profile.intra.42.fr/users/auth/keycloak_student/callback'
        self.log(f'Creating Intra session...', INFO)
        session = requests.Session()
        self.log(f'Getting sign in page ({sign_in_page_url}).', DEBUG)
        r_initial = session.get(sign_in_page_url)
        html_content = r_initial.text
        soup = BeautifulSoup(html_content, 'html.parser')
        element = soup.find(id='kc-form-login')
        authenticate_url = element.attrs.get('action', '') if isinstance(element, Tag) else ''
        if not authenticate_url:
            self.log(f'Could not extract authentication url (empty).', ERROR)
            return session
        self.log(f'Posting to authentication url ({authenticate_url}).', DEBUG)
        session.post(authenticate_url, data={'username': intra_login, 'password': intra_password})
        self.log(f'Getting callback url ({callback_url}).', DEBUG)
        session.get(callback_url)
        self.log(f'Intra session creation successful. Returning session.', INFO)
        return session

    def get_token(self, uid: str, secret: str):
        """Requests an application access token using uid and secret.
        """
        try:
            resp = requests.post('https://api.intra.42.fr/oauth/token', data={
                'grant_type': 'client_credentials',
                'client_id': uid,
                'client_secret': secret,
            })
        except KeyboardInterrupt:
            print(end='\n', file=sys.stdout, flush=True)
            self.log(f'Received KeyboardInterrupt.', FATAL)
            raise SystemExit(130)
        if resp.status_code == 200:
            resp = resp.json()
            expires_in = resp['expires_in']
            token = resp['access_token']
            self.log(f'- {token=} {expires_in=}s\n', DEBUG)
            return token
        return ''

    def get_credentials(self, app_id: str) -> tuple[str, str]:
        """Scrape uid and secret from a given API application from the intra.
        """
        self.log(f'Getting app credentials (uid, secret) for app "{app_id}".', DEBUG)
        resp = self.session.get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if resp.status_code != 200:
            self.log(f'Could not get page for app "{app_id}".', ERROR)
            return '', ''
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        secret_div = soup.find('div', {'data-copy': f'[data-app-secret-{app_id}]'}, class_='copy')
        secret = secret_div.get('data-clipboard-text', '') if isinstance(secret_div, Tag) else ''
        uid_div = soup.find('div', {'data-copy': f'[data-app-uid-{app_id}]'}, class_='copy')
        uid = uid_div.get('data-clipboard-text', '') if isinstance(uid_div, Tag) else ''
        if isinstance(uid, str) and isinstance(secret, str):
            self.log(f'Successfully scraped credentials for app "{app_id}".', DEBUG)
            return uid, secret
        return '', ''

    def get_app_ids(self) -> list[str]:
        """Scrape user API applications from the intra.
        """
        self.log(f'Getting app ids.', DEBUG)
        resp = self.session.get(f'https://profile.intra.42.fr/oauth/applications')
        if resp.status_code != 200:
            self.log(f'Could not get apps overview page.', ERROR)
            return []
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        apps_root = soup.find('div', class_='apps-root')
        apps_data = apps_root.get('data', '[]') if isinstance(apps_root, Tag) else []
        apps_data = json.loads(apps_data) if isinstance(apps_data, str) else []
        app_ids = [str(app_data['id']) for app_data in apps_data if 'id' in app_data]
        return app_ids

    def refresh_tokens(self):
        """Only refresh the access tokens (fast)
        """
        for app_id, app_creds in self.apps.items():
            self.log(f'Refreshing token for app "{app_id}".', INFO)
            uid = app_creds['uid']
            secret = app_creds['secret']
            token = self.get_token(uid, secret)
            self.apps[app_id]['token'] = token

    def update(self):
        """Fully update the credentials and tokens of all apps that are available
           through the intra.
        """
        self.session = self.create_intra_session(self.intra_login, self.intra_password)
        app_ids = self.get_app_ids()
        for app_id in app_ids:
            self.log(f'Adding app "{app_id}" to apps list.', INFO)
            uid, secret = self.get_credentials(app_id)
            self.log(f'- {uid=}', DEBUG)
            self.log(f'- {secret=}\n', DEBUG)
            self.apps[app_id]['uid'] = uid
            self.apps[app_id]['secret'] = secret
            self.apps[app_id]['id'] = app_id
        self.refresh_tokens()
