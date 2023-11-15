# ApiHydra
This is a small and WiP project to load-balance any API in a multi-threaded
and client-side fashion. The only currently implemented API is the 42 Intra API.

## Getting started
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

The default parameter `api_base: str = 'https://api.intra.42.fr/v2'`{:.python}
