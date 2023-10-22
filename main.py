#!/usr/bin/env python3

import os
import re
import logging
from base64 import b64decode

from dotenv import load_dotenv
from ApiHydra import FtApiHydra

def load_users_from_file(file_name: str) -> list[str]:
    with open(file_name, 'r', encoding='utf-8') as f:
        return list(map(str.strip, re.sub(r'\n+', r'\n', f.read()).splitlines()))

def main() -> int:
    load_dotenv()
    INTRA_LOGIN = os.environ.get('INTRA_LOGIN', '')
    INTRA_PW_B64 = os.environ.get('INTRA_PW_B64', '')
    INTRA_PW = b64decode(INTRA_PW_B64.encode()).decode()

    hydra = FtApiHydra(
        max_retries=100,
        log_level=logging.DEBUG,
        intra_login=INTRA_LOGIN,
        intra_password=INTRA_PW,
    )
    # hydra.update()
    hydra.refresh_tokens()

    hydra.get(f'https://api.intra.42.fr/v2/campus?per_page=100&page=1')

    resps = hydra.get_responses()

    for resp in resps:
        print(resp)

    return 0

    logins = load_users_from_file('../42_users/all_logins_berlin.txt')
    logins = logins[:200]

    for login in logins:
        hydra.get(f'https://api.intra.42.fr/v2/users/{login}')

    hydra.join()
    print()
    with open('./output4.json', 'w') as f:
        json.dump(hydra.responses, f, indent=4)
    print('Done')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
