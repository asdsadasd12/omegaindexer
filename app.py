import json
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup


OMEGA_API_URL = "https://app.omegaindexer.com/api/omega-indexer-api"
GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html)"
)


@dataclass
class OmegaCampaignPayload:
    apikey: str
    campaignname: str
    urls: str
    dripfeed: str

    def as_dict(self) -> dict[str, str]:
        return {
            "apikey": self.apikey,
            "campaignname": self.campaignname,
            "urls": self.urls,
            "dripfeed": self.dripfeed,
        }


def normalize_url(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if not parsed.scheme:
        candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    return candidate.rstrip("/")


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def build_pipe_delimited_urls(urls: Iterable[str]) -> str:
    return "|".join(urls)


def fetch_homepage_links(site_url: str, timeout: int = 20) -> list[str]:
    normalized_site = normalize_url(site_url)
    site_host = urlparse(normalized_site).netloc

    response = requests.get(
        normalized_site,
        headers={"User-Agent": GOOGLEBOT_UA},
        timeout=timeout,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    collected: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        absolute_url = urljoin(normalized_site + "/", href)
        parsed = urlparse(absolute_url)

        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != site_host:
            continue

        cleaned = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if cleaned:
            collected.add(cleaned)

    return sorted(collected)


def send_to_omega(payload: OmegaCampaignPayload) -> tuple[bool, str]:
    response = requests.post(
        OMEGA_API_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload.as_dict()),
        timeout=30,
    )
    body = response.text.strip()

    if response.ok:
        return True, body or "Campaign created."
    return False, body or f"HTTP {response.status_code}"


def render_payload_help() -> None:
    st.info(
        "OmegaIndexer API expects four string fields: "
        "`apikey`, `campaignname`, `urls`, `dripfeed`. "
        "`urls` must be pipe-delimited and `dripfeed` is capped at 30 days."
    )


def main() -> None:
    st.set_page_config(page_title="OmegaIndexer Helper", page_icon="O")
    st.title("OmegaIndexer Streamlit Helper")
    st.caption(
        "Upload URLs manually or collect homepage links from multiple sites "
        "before sending them to OmegaIndexer."
    )

    with st.sidebar:
        st.header("Connection")
        api_key = st.text_input("OmegaIndexer API key", type="password")
        dripfeed_days = st.number_input(
            "Drip-feed days",
            min_value=1,
            max_value=30,
            value=7,
            step=1,
        )
        request_timeout = st.number_input(
            "Homepage fetch timeout (sec)",
            min_value=5,
            max_value=60,
            value=20,
            step=1,
        )
        render_payload_help()

    manual_tab, crawl_tab = st.tabs(
        ["Manual URL submission", "Collect from homepage links"]
    )

    with manual_tab:
        st.subheader("Send your own URLs")
        campaign_name = st.text_input(
            "Campaign name",
            value="Manual campaign",
            key="manual_campaign_name",
        )
        raw_urls = st.text_area(
            "URLs (one per line)",
            height=220,
            placeholder="https://example.com/page-1\nhttps://example.com/page-2",
        )

        if st.button("Send URLs to OmegaIndexer", type="primary"):
            try:
                urls = [normalize_url(url) for url in split_lines(raw_urls)]
            except ValueError as exc:
                st.error(str(exc))
            else:
                if not api_key:
                    st.error("Enter your OmegaIndexer API key in the sidebar.")
                elif not urls:
                    st.error("Add at least one URL.")
                else:
                    payload = OmegaCampaignPayload(
                        apikey=api_key,
                        campaignname=campaign_name.strip() or "Manual campaign",
                        urls=build_pipe_delimited_urls(urls),
                        dripfeed=str(dripfeed_days),
                    )
                    success, message = send_to_omega(payload)
                    if success:
                        st.success("Campaign request sent successfully.")
                        st.code(message)
                    else:
                        st.error("OmegaIndexer rejected the request.")
                        st.code(message)

    with crawl_tab:
        st.subheader("Collect internal links from site homepages")
        sites_input = st.text_area(
            "Sites (one domain per line)",
            height=180,
            placeholder="example.com\nanother-example.com",
        )
        crawl_campaign_name = st.text_input(
            "Campaign name",
            value="Homepage crawl campaign",
            key="crawl_campaign_name",
        )

        if st.button("Fetch homepage links"):
            sites = split_lines(sites_input)
            if not sites:
                st.error("Add at least one site.")
            else:
                results: dict[str, dict[str, object]] = {}
                all_urls: list[str] = []

                for site in sites:
                    try:
                        links = fetch_homepage_links(
                            site,
                            timeout=int(request_timeout),
                        )
                    except Exception as exc:  # noqa: BLE001
                        results[site] = {"error": str(exc), "links": []}
                    else:
                        results[site] = {"error": "", "links": links}
                        all_urls.extend(links)

                st.session_state["crawl_results"] = results
                st.session_state["crawl_urls"] = sorted(set(all_urls))

        crawl_results = st.session_state.get("crawl_results")
        crawl_urls = st.session_state.get("crawl_urls", [])

        if crawl_results:
            st.markdown("### Results")
            for site, site_result in crawl_results.items():
                error = site_result["error"]
                links = site_result["links"]
                if error:
                    st.warning(f"{site}: {error}")
                else:
                    st.write(f"{site}: found {len(links)} internal links")

            st.markdown(f"### Total unique URLs: {len(crawl_urls)}")
            st.text_area(
                "Collected URLs",
                value="\n".join(crawl_urls),
                height=220,
                disabled=True,
            )

            if st.button("Send collected URLs to OmegaIndexer", type="primary"):
                if not api_key:
                    st.error("Enter your OmegaIndexer API key in the sidebar.")
                elif not crawl_urls:
                    st.error("No URLs were collected yet.")
                else:
                    payload = OmegaCampaignPayload(
                        apikey=api_key,
                        campaignname=(
                            crawl_campaign_name.strip()
                            or "Homepage crawl campaign"
                        ),
                        urls=build_pipe_delimited_urls(crawl_urls),
                        dripfeed=str(dripfeed_days),
                    )
                    success, message = send_to_omega(payload)
                    if success:
                        st.success("Collected URLs sent successfully.")
                        st.code(message)
                    else:
                        st.error("OmegaIndexer rejected the request.")
                        st.code(message)


if __name__ == "__main__":
    main()
