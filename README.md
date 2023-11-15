# ApiHydra
This is a small and WiP project to load-balance any API in a multi-threaded and
client-side fashion (similar to lunar.dev). The only currently implemented
ApiHydra is for the 42 Intra API. If tuned correctly, it allows for hundreds,
even thousand(s) of requests per second, although if you want to be
nice, don't do that. There's are many fallback and failsafe mechanisms
built in, such that long running programs don't suddenly lose all the
data because of one exception. It also handles big memory issues when
you're making a very large number of requests (100K+). It will dump
every batch of 10K requests to disk and start anew with empty memory.
Load-balancing is done on a rotating-token basis, so start the Hydra
again and again will not load balance the usage. For non-api related
requests, there's a 2 second infinite retry delay. For api related
requests, there's an optional constructor argument `max_retries` that
is 50 by default. For 404's, this limit is 5 instead. The delay
increases exponentially. If you have a large number of apps (100+),
the theoretical delay might between requests might be very small, so
to not DOS the server, please use the `min_request_delay` parameter
sensibly. The `requests_per_second` parameter should be set to the API
limit per second per app, or a little bit less for safety.
If the program exits without calling the .finish() method, it will try
to serialize the responses to disk, first as json, if that fails, then by
calling the str() method, and if that fails too, it'll try both things again
but on `/tmp`.

## Prerequisites
```sh
python3 -m venv ./env # or virtualenv ./env
. ./env/bin/activate
pip install -r requirements.txt
```

## Quickstart (FtApiHydra, demo in main.py)
First, make sure that you have 2FA disabled. After that, you can
import the module and create an instance of the hydra with the parameters you need.
For an already pretty fast client you can use these parameters:
```python
#! /usr/bin/env python3

import logging
from FtApiHydra import FtApiHydra

hydra = FtApiHydra(
    stats=True,
    max_retries=50,
    requests_per_second=1.9,
    min_request_delay=.02,
    log_level=logging.INFO,
    intra_login='your_login',
    intra_password='your_plain_password',
    responses_file_path_template='./output_%s_%s.json',
)
```

The default parameter `api_base: str = 'https://api.intra.42.fr/v2'`
ensures that you only have to type the important part of the endpoint.
So to make 1000 requests, let's say for the users of 42 Berlin, this is all you need
(main.py contains fuller example):
```python
import json

hydra.set_number_of_apps(10)
logins = ['user1', 'user3', 'user2', 'user4', 'user5'] * 200 # dummy logins
for login in logins:
    hydra.get(f'/users/{login}')
data = hydra.get_responses_as_json()

with open('output.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False)

hydra.finish()
```
The call `hydra.set_number_of_apps(10)` will ensure that you have exactly 10
apps to make API requests with. Remember, the rate limit is 2reqs/second/app.
The final `hydra.finish()` is important, because this tell the hydra to not
save the responses in an emergency output file (specified by the `reponses_file_path_template` argument).
This is very practical when the process takes long to finish and you have to press CTRL+C
or when the program unexpectedly exits.

If there are unexpected behaviours, first make sure to set the log_level to
`logging.DEBUG` (other possible values are `.WARNING`, `.ERROR`, and `.FATAL`).
If that doesn't work, try to find the error. Fix it. Make a pull requests.
Otherwise make an issue. Frankly, the code is bad, but there is some
documentation about what the methods do, feel free to read it.
