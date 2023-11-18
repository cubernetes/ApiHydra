#!/usr/bin/env python3

import os
import json
import logging
from base64 import b64decode

from dotenv import load_dotenv
from FtApiHydra import FtApiHydra


load_dotenv()
INTRA_LOGIN = os.environ.get('INTRA_LOGIN', '')
# generate like this on gnu/linux (to avoid newline and shell history):
# read -p 'INTRA PW:' -rd$'\n' pw_ && echo -n "$pw_" | base64 -w0 && unset pw_
# so you don't leak your pw by opening .env while others are watching
INTRA_PW_B64 = os.environ.get('INTRA_PW_B64', '')
INTRA_PW = b64decode(INTRA_PW_B64.encode()).decode()

hydra = FtApiHydra(
    stats=True,
    max_retries=50,
    requests_per_second=1.9,
    min_request_delay=.01,
    log_level=logging.INFO,
    intra_login=INTRA_LOGIN,
    intra_password=INTRA_PW,
    responses_file_path_template='./output_%s_%s.json',
)

hydra.set_number_of_apps(50)

campus = 'berlin'
max_pages = 50
for page in range(1,21): # we have less than 5000 == 50*100 users in berlin
    print(end=f'\rGetting users from campus "{campus}", page: {page}/{max_pages}          ')
    hydra.get(f'/campus/{campus}/users?page={page}&per_page=100')
resps = hydra.get_responses()

logins_42berlin = []
for resp in resps:
    for user in resp[1].json():
        if 'login' in user:
            logins_42berlin.append(user['login'])

print(f'Number of accounts in 42berlin: {len(logins_42berlin)}')

hydra.clear_responses()
for i, login in enumerate(logins_42berlin):
    print(end=f'\rGetting account data: {i+1}/{len(logins_42berlin)}            ')
    hydra.get(f'/users/{login}')
resps = hydra.get_responses_as_json()

with open('output.json', 'w', encoding='utf-8') as f:
    json.dump(resps, f, indent=4, ensure_ascii=False)

print(f'Done')

hydra.finish()
