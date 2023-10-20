#!/usr/bin/env python3

import os
import sys
import json
import requests
from typing import TextIO
from base64 import b64decode
from collections import defaultdict

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv


class ApiHydra:
    def __init__(self, log_level: int=1, log_file: TextIO=sys.stdout):
        self.log_level = log_level
        self.log_file = log_file

    def log(self, msg: str, log_level: int=1):
        if log_level <= self.log_level:
            print(msg, file=self.log_file, flush=True)

class FtApiHydra(ApiHydra):
    def __init__(
            self,
            intra_login: str,
            intra_password: str,
            log_level: int=1,
            log_file: TextIO=sys.stdout,
            intra_apps_file_path: str='./intra_apps.json'
        ) -> None:
        super().__init__(log_level, log_file)
        self.session = self.create_intra_session(intra_login, intra_password)
        self.log('Done with session creation.', 3)
        self.intra_apps_file_path = intra_apps_file_path
        self.apps = defaultdict(dict)
        try:
            with open(self.intra_apps_file_path, 'r') as intra_apps_file:
                try:
                    self.log(f"Deserializing from {self.intra_apps_file_path}.", 1)
                    self.apps = defaultdict(dict, json.load(intra_apps_file))
                except:
                    self.log(f"Could not deserialize from {self.intra_apps_file_path}.", 1)
        except IOError:
            self.log("Couldn't open {self.intra_apps_file_path}.", 3)

    def __del__(self):
        with open(self.intra_apps_file_path, 'w') as intra_apps_file:
            self.log(f"Serializing to {self.intra_apps_file_path}.", 3)
            json.dump(self.apps, intra_apps_file)

    def create_intra_session(self, intra_login: str, intra_password: str) -> requests.Session:
        sign_in_page_url = 'https://profile.intra.42.fr/users/auth/keycloak_student'
        callback_url = 'https://profile.intra.42.fr/users/auth/keycloak_student/callback'
        session = requests.Session()
        self.log(f'Getting sign in page: {sign_in_page_url}.', 3)
        r_initial = session.get(sign_in_page_url)
        html_content = r_initial.text
        soup = BeautifulSoup(html_content, 'html.parser')
        element = soup.find(id='kc-form-login')
        authenticate_url = element.attrs.get('action', '') if isinstance(element, Tag) else ''
        if not authenticate_url:
            self.log('Could not extract authentication url (empty).')
            return session
        self.log(f'Posting to authentication url: {authenticate_url}.', 3)
        session.post(authenticate_url, data={'username': intra_login, 'password': intra_password})
        self.log(f'Getting callback url: {callback_url}.', 3)
        session.get(callback_url)
        self.log('Returning session.', 3)
        return session

    def get_credentials(self, app_id: str) -> tuple[str, str]:
        self.log('Getting creds.', 3)
        resp = self.session.get(f'https://profile.intra.42.fr/oauth/applications/{app_id}')
        if resp.status_code != 200:
            self.log("Couldn't get creds.")
            self.log(resp.text[:200])
            return '', ''
        self.log('Got creds resp.', 3)
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        secret_div = soup.find('div', {'data-copy': f'[data-app-secret-{app_id}]'}, class_='copy')
        secret = secret_div.get('data-clipboard-text', '') if isinstance(secret_div, Tag) else ''
        uid_div = soup.find('div', {'data-copy': f'[data-app-uid-{app_id}]'}, class_='copy')
        uid = uid_div.get('data-clipboard-text', '') if isinstance(uid_div, Tag) else ''
        if isinstance(uid, str) and isinstance(secret, str):
            return uid, secret
        return '', ''

    def get_app_ids(self) -> list[str]:
        self.log('Getting ids.', 3)
        resp = self.session.get(f'https://profile.intra.42.fr/oauth/applications')
        if resp.status_code != 200:
            self.log("Couldn't get app id page.")
            self.log(resp.text[:200])
            return []
        self.log('Got ids.', 3)
        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        apps_root = soup.find('div', class_='apps-root')
        self.log(f'{apps_root=}', 3)
        apps_data = apps_root.get('data', '[]') if isinstance(apps_root, Tag) else []
        self.log(f'{apps_data}', 3)
        apps_data = json.loads(apps_data) if isinstance(apps_data, str) else []
        self.log(f'{apps_data=}', 3)
        app_ids = [app_data['id'] for app_data in apps_data if 'id' in app_data]
        return app_ids

    def update(self):
        app_ids = self.get_app_ids()
        for app_id in app_ids:
            uid, secret = self.get_credentials(app_id)
            self.log(f'{app_id=}', 3)
            self.log(f'{uid=}', 2)
            self.log(f'{secret=}', 2)
            self.apps[app_id]['uid'] = uid
            self.apps[app_id]['secret'] = secret
            if 'last_request' not in self.apps[app_id]:
                self.apps[app_id]['last_request'] = 0

def main() -> int:
    load_dotenv()
    INTRA_LOGIN = os.environ.get('INTRA_LOGIN', '')
    INTRA_PW_B64 = os.environ.get('INTRA_PW_B64', '')
    INTRA_PW = b64decode(INTRA_PW_B64.encode()).decode()

    hydra = FtApiHydra(INTRA_LOGIN, INTRA_PW, log_level=1)
    hydra.update()
    for app_id, app_data in hydra.apps.items():
        print(app_id, app_data)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
