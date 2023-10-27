#!/usr/bin/env python3

import os
import re
import json
import logging
from base64 import b64decode

from dotenv import load_dotenv
from ApiHydra import FtApiHydra

def load_users_from_file(file_name: str) -> list[str]:
    with open(file_name, 'r', encoding='utf-8') as f:
        return list(map(str.strip, re.sub(r'\n+', r'\n', f.read()).splitlines()))

def get_all_users_by_campus_users(hydra: FtApiHydra):
    print('Getting users per campus_users')
    per_page = 100
    batch = 1
    batch_size = len(hydra.apps)

    data = []

    while True:
        if batch > 3:
            return
        for page in range((batch - 1) * batch_size + 1, batch * batch_size + 1):
            print(end=f'\r{page=:>3}/{batch * batch_size} ({batch=})' + ' '*10)
            hydra.get(f'/campus_users?per_page={per_page}&page={page}')
        print()
        resps = hydra.get_responses()
        for d in [resp[1].json() for resp in resps[-batch:] if resp[1].json()]:
            data += d
        for resp in resps[-batch:]:
            if len(resp[1].json()) != per_page:
                print('Done')
                with open('./output.json', 'w', encoding='utf-8') as output_file_writer:
                    json.dump(data, output_file_writer, indent=4, ensure_ascii=False)
                return
        batch += 1

def get_all_users_42berlin(hydra: FtApiHydra):
    def get_list_of_users() -> list[str]:
        hydra.log('Getting all logins of 42Berlin...', logging.INFO)
        per_page = 100
        batch = 1
        campus_id = 51 # 51 == Berlin
        batch_size = len(hydra.apps)
        data = []
        while True:
            for page in range((batch - 1) * batch_size + 1, batch * batch_size + 1):
                print(end=f'\r{page=:>3}/{batch * batch_size} ({batch=})' + ' '*10)
                hydra.get(f'/campus/{campus_id}/users?per_page={per_page}&page={page}')
            print()
            resps = hydra.get_responses_copy()
            hydra.clear_responses()
            for d in [resp[1].json() for resp in resps if resp[1].json()]:
                data += d
            for resp in resps:
                if len(resp[1].json()) != per_page:
                    hydra.log('Got all logins.', logging.INFO)
                    logins = []
                    for u in data:
                        logins.append(u['login'])
                    return logins
            batch += 1
    logins = get_list_of_users()
    hydra.clear_responses()
    for i, login in enumerate(logins):
        print(end=f'\r{i=:>3}/{len(logins)} ({login=})' + ' '*10)
        hydra.get(f'/users/{login}')
    resps = hydra.get_responses()
    data = []
    for resp in resps:
        data.append(resp[1].json())

    with open('./output.json', 'w', encoding='utf-8') as output_file_writer:
        json.dump(data, output_file_writer, indent=4, ensure_ascii=False, sort_keys=True)

def get_all_users_by_campus(hydra: FtApiHydra):
    print('Getting campuses')
    hydra.clear_responses()
    hydra.get(f'/campus?per_page=100&page=2')

    resps = hydra.get_responses()
    print('Done')

    resp = resps[0]
    campuses = resp[1].json()
    campus_ids = [campus['id'] for campus in campuses]

    print('Getting users per campus')
    per_page = 100
    page = 1
    hydra.clear_responses()
    for i, campus_id in enumerate(campus_ids):
        print(end=f'\r{i+1}/{len(campus_ids)}: {campus_id=}' + ' '*10)
        hydra.get(f'/campus/{campus_id}/users?per_page={per_page}&page={page}')
    page += 1
    print()

    data = []

    paginated = True
    while paginated:
        resps = hydra.get_responses_copy()
        hydra.clear_responses()
        paginated = False
        for i, (url, resp) in enumerate(resps):
            resp = resp.json()
            data.append((url, resp))
            if len(resp) == per_page:
                paginated = True
                campus_id = url.split('/campus/', 1)[1].split('/', 1)[0]
                print(end=f'\r{i+1}/{len(resps)} {campus_id=}' + ' '*10)
                hydra.get(f'/campus/{campus_id}/users?per_page={per_page}&page={page}')
        print()
        page += 1

    print('Done')
    with open('./output.json', 'w', encoding='utf-8') as output_file_writer:
        json.dump(data, output_file_writer, indent=4, ensure_ascii=False, sort_keys=True)

def get_literally_all_users(hydra: FtApiHydra):
    login_ids = []
    with open('./all_campus_users.json') as all_campus_users_reader:
        campus_users = json.load(all_campus_users_reader)
    for campus_user in campus_users:
        login_ids.append(campus_user['user_id'])
    hydra.clear_responses()
    for i, login_id in enumerate(login_ids):
        print(end=f'\r{i=:>3}/{len(login_ids)} ({login_id=})' + ' '*10)
        hydra.get(f'/users/{login_id}')
    resps = hydra.get_responses()
    data = []
    for resp in resps:
        data.append(resp[1].json())

    with open('./output.json', 'w', encoding='utf-8') as output_file_writer:
        json.dump(data, output_file_writer, indent=4, ensure_ascii=False, sort_keys=True)

def main() -> int:
    load_dotenv()
    INTRA_LOGIN = os.environ.get('INTRA_LOGIN', '')
    INTRA_PW_B64 = os.environ.get('INTRA_PW_B64', '')
    INTRA_PW = b64decode(INTRA_PW_B64.encode()).decode()

    hydra = FtApiHydra(
        stats=True,
        max_retries=100,
        requests_per_second=1.9,
        min_request_delay=.03,
        log_level=logging.INFO,
        intra_login=INTRA_LOGIN,
        intra_password=INTRA_PW,
        responses_file_path_template='./output_%s.json',
    )

    # hydra.update()
    # hydra.refresh_tokens()

    # get_all_users_by_campus(hydra)
    # get_all_users_by_campus_users(hydra)
    # get_all_users_42berlin(hydra)
    # hydra.get('/cursus/21/projects?filter[name]=Libft')
    # hydra.get('/users/dlucio')
    # hydra.get('/projects/42cursus-snow-crash')
    # hydra.print_api_usage(update=False)
    get_literally_all_users(hydra)
    hydra.print_api_usage(update=True)

    # print(json.dumps(hydra.get_responses()[0][1].json(), indent=4))

    # with open('./all_core_projects_42berlin.csv') as f:
    #     projects = {line.split(';')[0] : {'name':line.split(';')[1]} for line in f.read().splitlines()}
    # hydra.clear_responses()
    # for project in projects:
    #     hydra.get(f'/projects/{project}')
    # resps = hydra.get_responses()
    # for resp in resps:
    #     p_url, resp = resp
    #     p_id = p_url.rsplit('/', 1)[1]
    #     xp = resp.json()['difficulty']
    #     projects[p_id]['xp'] = xp
    # print(json.dumps(projects, indent=4, ensure_ascii=False))

    # with open('./all_users_42berlin.json') as f:
    #     users = json.load(f)
    # with open('./projects_xp.json') as f:
    #     projects_xp = json.load(f)
    # for i in range(len(users)):
    #     user = users[i]
    #     login = user['login']
    #     projects_users = user['projects_users']
    #     if 'xp' not in users[i]:
    #         users[i]['xp'] = 0
    #     for projects_user in projects_users:
    #         if 21 in projects_user['cursus_ids']:
    #             project_id = str(projects_user['project']['id'])
    #             if project_id in projects_xp:
    #                 project_xp = int(projects_xp[project_id]['xp'])
    #                 users[i]['xp'] += project_xp
    #             else:
    #                 pass
    # print(json.dumps(users, indent=4))

    hydra.finish()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
