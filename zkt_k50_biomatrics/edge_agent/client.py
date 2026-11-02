from urllib.parse import urlsplit

import requests


class Client:
    def __init__(self, url, credential):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Configure a clean HTTPS site URL without embedded credentials")
        self.url = url.rstrip("/")
        self.credential = credential
        self.session = requests.Session()

    def call(self, method, **payload):
        response = self.session.post(
            self.url + "/api/method/zkt_k50_biomatrics.agent_api." + method,
            headers={"X-ZKT-Credential": self.credential},
            json=payload,
            timeout=(10, 90),
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            # Do not log response bodies: reverse proxies can echo request headers.
            raise RuntimeError(f"Server rejected {method}: HTTP {response.status_code}")
        return response.json()["message"]
