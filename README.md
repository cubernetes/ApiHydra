# ApiHydra
This is a small and WiP project to load-balance any API in a multi-threaded
and client-side fashion. The only currently implemented API is the 42 Intra API.

## Getting started
First, make sure that you have 2FA disabled. 2FA might be supported later.
Import the module and create an instance of the hydra with the parameters you need.
For an already pretty fast client you can use these parameters:

```python
import loggin
from FtApiHydra import FtApiHydra

hydra = FtApiHydra(
    stats=True,
    max_retries=100,
    requests_per_second=1.9,
    min_request_delay=.02,
    log_level=logging.INFO,
    intra_login=INTRA_LOGIN,
    intra_password=INTRA_PW,
    responses_file_path_template='./output_%s_%s.json',
)
```

The default parameter `api_base: str = 'https://api.intra.42.fr/v2'`
ensures that you only have to type the important part of the endpoint.
So to make 1000 requests, let's say for the users of 42 Berlin, this is all you need:
```python
import json

hydra.set_number_of_apps(10)
for login in logins:
    hydra.get(f'/users/{login}')
data = hydra.get_responses_as_json()

with open('output.json', 'w', encoding='utf-8') as f:
    json.write(data, f, ensure_ascii=False)

hydra.finish()
```
The call `hydra.set_number_of_apps(10)` will ensure that you have exactly 10
apps to make API requests with. Remember, the rate limit is 2reqs/second/app.
The final hydra.finish() is important, because this tell the hydra to not
save the responses in an emergency output file (specified by the `reponses_file_path_template` argument).
This is very practical when the process takes long to finish and you have to press CTRL+C
or when the program unexpectedly exits.
