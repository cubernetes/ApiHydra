import os
import re
import sys
import copy
import json
import time
import threading
from datetime import datetime
from typing import Any, TextIO
from logging import DEBUG, INFO, WARNING, ERROR

import requests
from bs4 import BeautifulSoup, Tag

from ApiHydra import *


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
            response_serialization_part: int=1,
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
            responses_file_path_template=responses_file_path_template,
            response_serialization_part=response_serialization_part,
        )
        self.intra_login = intra_login
        self.intra_password = intra_password
        self.session = requests.Session()
        self.is_updated = False

    def ensure_session(self):
        """Checks if self.session has the 'user.id' cookie set,
           if not, creates new session.
        """
        if '_intra_42_session_production' in self.session.cookies:
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
           This method should not be run in the main thread.
        """
        app_id = app.get('id', '')
        if app_id and self.refresh_tokens_flag:
            while self.refresh_tokens_flag:
                time.sleep(0.5)
            app = self.apps[app_id]
        token = app.get('access_token', '')
        expires_in = app.get('token_expires_in', None)
        if not expires_in:
            self.log(f'"expires_in" value for app "{app_id}" did not exist.', ERROR)
            expires_in = time.time() + 10000
        if not app_id:
            self.log(f'Intra app has no id.', ERROR)
        else:
            if os.path.isfile(EMERGENCY_STOP_FILE):
                self.log(f'Found emergency stop file "{EMERGENCY_STOP_FILE}", exiting thread "{threading.current_thread().name}".', WARNING)
                raise SystemExit(42)
            if expires_in <= time.time():
                self.log(f'Token for app "{app_id}" expired ({time.time() - expires_in}s ago).', WARNING)
                self.log(f'Setting self.refresh_tokens_flag to True.', DEBUG)
                self.refresh_tokens()
                self.log(f'Setting self.refresh_tokens_flag to False.', DEBUG)
                return self.make_request_kwargs_from_app(self.apps[app_id], **kwargs)
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
        elif 'id="reset-password"' in callback_resp.text:
            self.log(f'Could not get callback url ({callback_resp.status_code}) to create session, maybe you have 2FA enabled?.', ERROR)
        else:
            self.log(f'Intra session creation successful.', INFO)

    def get_token(self, app_id: str, uid: str, secret: str) -> dict:
        """Requests an application access token using uid and secret.
        """
        resp = self.requests_post('https://api.intra.42.fr/oauth/token', data={
            'grant_type': 'client_credentials',
            'client_id': uid,
            'client_secret': secret,
        })
        if resp.status_code != 200:
            self.log(f'Could not get access token ({resp.status_code}) for app "{app_id}".', ERROR)
            return {'access_token': '', 'token_expires_in': -1}
        else:
            resp = resp.json()
            return resp

    def update_app(self, app_id: str) -> None:
        """Scrape uid and secret from a given API application from the intra.
        """
        self.ensure_session()
        self.log(f'Getting app credentials (uid, secret) for app "{app_id}".', DEBUG)
        app_page = self.requests_get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if app_page.status_code != 200:
            self.log(f'Could not get page ({app_page.status_code}) to update app "{app_id}".', ERROR)
            if app_page.status_code == 429:
                self.log(f'Waiting 2 seconds to overcome rate limiting.', WARNING)
                time.sleep(2)
                self.log(f'Trying again to update app "{app_id}".', WARNING)
                self.delete_app(app_id)
            return
        html = app_page.text
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
        self.refresh_tokens_flag = True
        for i, (app_id, app_creds) in enumerate(copy.deepcopy(self.apps).items()):
            self.log(f'Refreshing token for app "{app_id}" ({i+1}/{len(self.apps)}).', INFO)
            uid = app_creds.get('uid', '')
            secret = app_creds.get('secret', '')
            if not uid or not secret:
                self.log(f'App "{app_id}" has no {"uid" if not uid else "uid nor secret" if not secret else "secret"}, deleting it.', ERROR)
                del self.apps[app_id]
                continue
            token_resp = self.get_token(app_id, uid, secret)
            access_token = token_resp.get('access_token', '')
            expires_in = int(time.time()) + int(token_resp['expires_in'])
            self.apps[app_id]['access_token'] = access_token
            self.apps[app_id]['token_expires_in'] = expires_in
        self.refresh_tokens_flag = False

    def delete_app(self, app_id: str) -> None:
        """Delete API intra app by id.
        """
        self.ensure_session()
        app_page = self.requests_get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if app_page.status_code != 200:
            self.log(f'Could not get page ({app_page.status_code}) to delete app "{app_id}".', ERROR)
            if app_page.status_code == 429:
                self.log(f'Waiting 2 seconds to overcome rate limiting.', WARNING)
                time.sleep(2)
                self.log(f'Trying again to delete app "{app_id}".', WARNING)
                self.delete_app(app_id)
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
        if update:
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
        if update:
            self.update()
        return len(self.apps)

    def set_number_of_apps(self, number_of_apps: int, *, update: bool=True) -> None:
        if update:
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
            for app_id in list(self.apps)[:-diff]:
                self.delete_app(app_id)
            self.log(f'Deleted {-diff} apps, new app count is {len(self.apps)}.', INFO)
        else:
            self.log(f'Creating {diff} apps...', DEBUG)
            for _ in range(diff):
                self.create_app(update=False)
            self.log(f'Created {diff} apps, new app count is {len(self.apps)}.', INFO)
        self.update()

    def get_total_number_of_requests(self, *, update: bool=True) -> int:
        if update:
            self.update()
        total_requests = 0
        for app in self.apps.values():
            total_requests += app['total_requests']
        return total_requests

    def get_requests_left_this_hour(self, *, update: bool=True) -> tuple[int, int]:
        if update:
            self.update()
        requests_left = 0
        max_requests = 0
        for app in self.apps.values():
            requests_left += app['max_requests_per_hour'] - app['requests_last_hour']
            max_requests += app['max_requests_per_hour']
        return requests_left, max_requests

    def get_responses_as_json(self):
        """Construct and return a list of json objects of the responses.
           Errors are skipped.
        """
        self.join()
        json_resps = []
        for resp in self.responses:
            try:
                json_resps.append((resp[0], resp[1].json()))
            except json.decoder.JSONDecodeError as exc:
                self.log(f'Data Loss: Could not deserialize response from URL "{resp[0]}".', WARNING)
        self.log(f'Returning responses as json.', DEBUG)
        return json_resps

    def print_api_usage(self, *, update: bool=True) -> None:
        """Print the number of requests left, the maximum number of request that
           can be made per hour and how many were made.
        """
        if update:
            self.update()
        left, max = self.get_requests_left_this_hour(update=update)
        print(f'{left} out of {max} API requests left ({max-left} were made)', flush=True)

    def update(self) -> None:
        """Fully update the credentials and tokens of all apps that are available
           through the intra.
        """
        self.log(f'Updating Hydra...', DEBUG)
        self.ensure_session()
        app_ids = self.get_app_ids()
        self.log(f'Number of Apps: {len(app_ids)}', DEBUG)
        self.apps.clear()
        for i, app_id in enumerate(app_ids):
            if not app_id:
                continue
            self.log(f'Adding updated app "{app_id}" to apps list ({i+1}/{len(app_ids)}).', INFO)
            self.apps[app_id]['id'] = app_id
            self.update_app(app_id)
        self.refresh_tokens()
        self.is_updated = True
