# ProfileOut Python SDK

Official synchronous Python client for the [ProfileOut API](https://docs.profileout.com).

Requires Python 3.10 or newer. No runtime dependencies.

## Install

```sh
pip install profileout
```

The installation command above becomes available after the first PyPI publication. Until then, install a local checkout with `pip install /path/to/profileout-python`.

## Quick start

Get an API key from the [ProfileOut console](https://console.profileout.com). The account must be active and email-verified, with sufficient credits.

```python
import os
from profileout import ProfileOut

client = ProfileOut(api_key=os.environ["PROFILEOUT_API_KEY"])
request = client.create_people_data({"linkedin_id": "example-person"})
response = (
    request if request["status"] in ("completed", "failed")
    else client.wait_for_people_data(request["request_id"])
)

if response["status"] == "failed":
    print(response.get("error"))
else:
    print(response["result"])
```

Responses are dictionaries with the API's original snake_case fields. Nested profile fields can vary.

## APIs

| Operation | Method |
| --- | --- |
| Submit / read a profile | `create_people_data(input)` / `get_people_data(request_id)` |
| Download profile image | `get_people_data_image(request_id)` → `bytes` |
| Upload / read a resume | `create_profile_parsing(path)` / `get_profile_parsing(request_id)` |
| Submit / read contact enrichment | `create_contact_enrichment(input)` / `get_contact_enrichment(request_id)` |
| Start / continue a search | `create_people_search({"message": ...})` / `continue_people_search(session_id, {"message": ...})` |
| Read a search | `get_people_search(session_id)` |
| Check service health | `get_health()` (does not send the API key) |

People Data and Contact Enrichment accept exactly one identity form:

```python
{"linkedin_id": "example-person"}
{"profile_url": "https://www.linkedin.com/in/example-person"}
{"platform": "github", "account_id": "example-person"}
```

Platforms: `linkedin`, `github`, `x`, `twitter`, `facebook`. Facebook numeric IDs must be strings.

### Resume upload

```python
from pathlib import Path

request = client.create_profile_parsing(Path("resume.pdf"))
response = client.wait_for_profile_parsing(request["request_id"])
print(response["status"], response["result"], response.get("error"))
```

Accepts a filesystem path, reads at most 10 MiB plus one byte to check the limit, and sends exactly one multipart `file` field. Files must be nonempty PDF, PNG, JPEG, or TIFF with a matching extension; the service validates the file signature.

### Search conversation

```python
session = client.create_people_search({"message": "Find senior backend engineers in Seoul."})
response = client.wait_for_people_search(session["session_id"])
print(response["status"], response["follow_up"], response["role_summary"], response["filters"])

# After reviewing the response, optionally send a new paid revision:
revision = client.continue_people_search(session["session_id"], {"message": "Require Python experience."})
print(revision["revision"])
```

Wait methods read the current request/session state; they do not pin a search revision. Avoid concurrently updating a session while waiting for a particular revision.

## Polling, errors, and timeouts

`wait_for_people_data`, `wait_for_profile_parsing`, `wait_for_contact_enrichment`, and `wait_for_people_search` accept an ID. They wait at least five seconds before the first GET and between pending results, honor longer `next_poll_after_seconds` hints, and return on either `completed` or `failed`. Always inspect `status`; HTTP 200 can contain a failed job, and search outputs can contain their own errors.

```python
client = ProfileOut(api_key=os.environ["PROFILEOUT_API_KEY"], timeout=120)
response = client.wait_for_people_data(request_id, timeout=1800)
```

Timeout values use **seconds**. The client default is a 120-second timeout for blocking network operations. Wait methods default to a 20-minute polling deadline and cap each request's socket timeout by the remaining time. Python's standard HTTP transport uses a per-operation socket timeout; a slowly streaming response can overrun the polling deadline until that read finishes. A wait deadline raises `TimeoutError`. Local timeouts or interruption do not cancel processing on the server. This client is synchronous; calls block the calling thread.

```python
from profileout import ProfileOutError

try:
    response = client.get_people_data(request_id)
    print(response["status"])
except ProfileOutError as error:
    print(error.status, error.code, error.request_id)
    # error.body contains the response; error.headers includes retry-after if present.
```

HTTP failures and invalid response objects raise `ProfileOutError`. Network errors use the standard library's `urllib.error.URLError`, `OSError`, or HTTP exceptions; invalid local inputs raise `ValueError` or `TypeError`.

**No requests are automatically retried.** Each accepted POST, including repeated submissions and search revisions, can reserve credits. After an ambiguous failure, recover a known request ID and use GET instead of blindly repeating POST. GET polling does not reserve additional credits. See [billing](https://docs.profileout.com/guides/billing).

The optional `base_url` defaults to `https://api.profileout.com`. HTTPS is required except on localhost; redirects are rejected so credentials and paid requests cannot be forwarded.

## Publishing to PyPI (maintainers)

Distribution and import name: `profileout`. Initial version: `0.1.0`. This repository is ready to package, but no registry account or project is created by cloning it.

1. [Create a PyPI account](https://pypi.org/account/register/), verify your email, and configure two-factor authentication.
2. Check whether [the project name](https://pypi.org/project/profileout/) is available. If another owner takes it, change `project.name` in `pyproject.toml` and installation examples; the Python import can remain `profileout`.
3. Create a PyPI API token with upload access. For a first upload, the project does not yet exist; use an account-scoped token, then replace it with a project-scoped token after publication. Store it outside the repository, and never paste it into chat.
4. From this repository, prepare build tools in a local virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install build twine
python -m build --outdir dist/0.1.0
python -m twine check dist/0.1.0/*
```

The standard build command installs the declared setuptools backend in an isolated build environment. Runtime installation of the SDK has no third-party dependencies.

5. Upload the verified wheel and source distribution:

```sh
python -m twine upload --repository-url https://upload.pypi.org/legacy/ dist/0.1.0/*
```

When prompted for an API token, paste your token including its `pypi-` prefix. If your Twine version asks for a username and password, use `__token__` as the username and your API token as the password. Uploading creates the project on the first successful publication. Build tools may also obtain credentials through the system keyring.

Confirm the release at [PyPI](https://pypi.org/project/profileout/). For each later release, change the version in `pyproject.toml` and use a matching, new output directory; do not upload old artifacts. A published name/version cannot be reused. The files that are published become public.

See the [official Python packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

## API compatibility

Targets the ProfileOut v1 public API. When that contract changes, review both SDKs against the service's OpenAPI specification and update methods and examples together.

## License

[MIT](LICENSE). The license covers SDK code; API access and credits are provided separately by ProfileOut.
