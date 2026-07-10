import json
from dataclasses import asdict
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
SITEMAP_PRIORITY_MARKER = "/sitemap-page"
DEFAULT_SITEMAP_PRIORITY_PATH = "/top/sitemap-page-1/"


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


@dataclass
class PageCheckResult:
    url: str
    final_url: str
    site: str
    status_code: int
    canonical_url: str
    is_sitemap: bool
    is_self_canonical: bool
    is_indexable: bool
    is_valid: bool
    reason: str


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


def deduplicate_urls(urls: Iterable[str]) -> tuple[list[str], int]:
    materialized_urls = list(urls)
    unique_urls = list(dict.fromkeys(materialized_urls))
    removed_duplicates = max(0, len(materialized_urls) - len(unique_urls))
    return unique_urls, removed_duplicates


def set_multiselect_selection(mode: str) -> None:
    crawl_urls = st.session_state.get("crawl_urls", [])
    selected_urls = st.session_state.get("selected_crawl_urls", [])

    if mode == "all":
        st.session_state["selected_crawl_urls"] = list(crawl_urls)
    elif mode == "none":
        st.session_state["selected_crawl_urls"] = []
    elif mode == "invert":
        selected_set = set(selected_urls)
        st.session_state["selected_crawl_urls"] = [
            url for url in crawl_urls if url not in selected_set
        ]


def normalize_comparable_url(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    path = parsed.path or "/"
    normalized_path = path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


def build_pipe_delimited_urls(urls: Iterable[str]) -> str:
    return "|".join(urls)


def build_start_url(site_url: str, start_path: str) -> str:
    normalized_site = normalize_url(site_url)
    cleaned_path = start_path.strip() or "/"
    if not cleaned_path.startswith("/"):
        cleaned_path = f"/{cleaned_path}"
    return urljoin(f"{normalized_site}/", cleaned_path.lstrip("/"))


def prioritize_and_limit_urls(
    urls: Iterable[str],
    max_urls: int,
    prioritize_sitemap: bool,
) -> list[str]:
    unique_urls, _ = deduplicate_urls(urls)
    if not prioritize_sitemap:
        return unique_urls[:max_urls]

    prioritized_urls = [
        url for url in unique_urls if SITEMAP_PRIORITY_MARKER in url.lower()
    ]
    regular_urls = [
        url for url in unique_urls if SITEMAP_PRIORITY_MARKER not in url.lower()
    ]
    ordered_urls = prioritized_urls + regular_urls
    return ordered_urls[:max_urls]


def build_priority_sitemap_url(site_url: str) -> str:
    normalized_site = normalize_url(site_url)
    return urljoin(f"{normalized_site}/", DEFAULT_SITEMAP_PRIORITY_PATH.lstrip("/"))


def has_noindex_directive(value: str) -> bool:
    directives = [part.strip().lower() for part in value.split(",") if part.strip()]
    return "noindex" in directives or "none" in directives


def validate_page_for_indexing(
    site: str,
    url: str,
    timeout: int = 20,
) -> PageCheckResult:
    normalized_requested = normalize_comparable_url(url)

    try:
        response = requests.get(
            url,
            headers={"User-Agent": GOOGLEBOT_UA},
            timeout=timeout,
            allow_redirects=True,
        )
        status_code = response.status_code
        final_url = normalize_comparable_url(response.url)
    except Exception as exc:  # noqa: BLE001
        return PageCheckResult(
            url=url,
            final_url="",
            site=site,
            status_code=0,
            canonical_url="",
            is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
            is_self_canonical=False,
            is_indexable=False,
            is_valid=False,
            reason=str(exc),
        )

    if status_code != 200:
        return PageCheckResult(
            url=url,
            final_url=final_url,
            site=site,
            status_code=status_code,
            canonical_url="",
            is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
            is_self_canonical=False,
            is_indexable=False,
            is_valid=False,
            reason="HTTP status is not 200",
        )

    if final_url != normalized_requested:
        return PageCheckResult(
            url=url,
            final_url=final_url,
            site=site,
            status_code=status_code,
            canonical_url="",
            is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
            is_self_canonical=False,
            is_indexable=False,
            is_valid=False,
            reason="URL redirects to another final URL",
        )

    soup = BeautifulSoup(response.text, "html.parser")
    canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    canonical_href = canonical_tag.get("href", "").strip() if canonical_tag else ""
    canonical_url = ""
    if canonical_href:
        canonical_url = normalize_comparable_url(urljoin(response.url, canonical_href))

    is_self_canonical = canonical_url == normalized_requested
    if not is_self_canonical:
        return PageCheckResult(
            url=url,
            final_url=final_url,
            site=site,
            status_code=status_code,
            canonical_url=canonical_url,
            is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
            is_self_canonical=False,
            is_indexable=False,
            is_valid=False,
            reason="Canonical is missing or not self-referencing",
        )

    x_robots = response.headers.get("X-Robots-Tag", "")
    robots_meta_values = []
    for meta_tag in soup.find_all("meta"):
        meta_name = (meta_tag.get("name") or meta_tag.get("property") or "").strip().lower()
        if meta_name in {"robots", "googlebot"}:
            robots_meta_values.append(meta_tag.get("content", ""))

    robots_blocked = has_noindex_directive(x_robots) or any(
        has_noindex_directive(value) for value in robots_meta_values
    )

    if robots_blocked:
        return PageCheckResult(
            url=url,
            final_url=final_url,
            site=site,
            status_code=status_code,
            canonical_url=canonical_url,
            is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
            is_self_canonical=True,
            is_indexable=False,
            is_valid=False,
            reason="Page is blocked from indexing by robots directives",
        )

    return PageCheckResult(
        url=url,
        final_url=final_url,
        site=site,
        status_code=status_code,
        canonical_url=canonical_url,
        is_sitemap=SITEMAP_PRIORITY_MARKER in url.lower(),
        is_self_canonical=True,
        is_indexable=True,
        is_valid=True,
        reason="OK",
    )


def fetch_homepage_links(
    site_url: str,
    start_path: str = "/",
    timeout: int = 20,
) -> list[str]:
    normalized_site = normalize_url(site_url)
    start_url = build_start_url(normalized_site, start_path)
    site_host = urlparse(normalized_site).netloc

    response = requests.get(
        start_url,
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

        absolute_url = urljoin(start_url, href)
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
        crawl_limit = st.number_input(
            "Max pages to keep",
            min_value=1,
            max_value=100,
            value=100,
            step=1,
        )
        prioritize_sitemap = st.checkbox(
            "Prioritize Sitemap Page URLs",
            value=True,
            help=(
                "Places URLs like `/sitemap-page-1/` at the top and keeps "
                "them first inside the page limit."
            ),
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
                normalized_urls = [
                    normalize_url(url) for url in split_lines(raw_urls)
                ]
            except ValueError as exc:
                st.error(str(exc))
            else:
                if not api_key:
                    st.error("Enter your OmegaIndexer API key in the sidebar.")
                elif not normalized_urls:
                    st.error("Add at least one URL.")
                else:
                    urls, removed_duplicates = deduplicate_urls(normalized_urls)
                    payload = OmegaCampaignPayload(
                        apikey=api_key,
                        campaignname=campaign_name.strip() or "Manual campaign",
                        urls=build_pipe_delimited_urls(urls),
                        dripfeed=str(dripfeed_days),
                    )
                    success, message = send_to_omega(payload)
                    if success:
                        st.success("Campaign request sent successfully.")
                        st.caption(
                            f"Unique URLs sent: {len(urls)}. "
                            f"Removed duplicates: {removed_duplicates}."
                        )
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
        start_path = st.text_input(
            "Page path to parse",
            value="/",
            help="Default is the main homepage of each site.",
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
                            start_path=start_path,
                            timeout=int(request_timeout),
                        )
                    except Exception as exc:  # noqa: BLE001
                        results[site] = {"error": str(exc), "links": []}
                    else:
                        if prioritize_sitemap:
                            links = [build_priority_sitemap_url(site)] + links
                        results[site] = {"error": "", "links": links}
                        all_urls.extend(links)

                unique_crawl_urls, removed_duplicates = deduplicate_urls(all_urls)
                valid_page_records: list[PageCheckResult] = []
                excluded_page_records: list[PageCheckResult] = []
                progress_bar = st.progress(0)

                if unique_crawl_urls:
                    for index, url in enumerate(unique_crawl_urls, start=1):
                        source_site = urlparse(url).netloc
                        check_result = validate_page_for_indexing(
                            source_site,
                            url,
                            timeout=int(request_timeout),
                        )
                        if check_result.is_valid:
                            valid_page_records.append(check_result)
                        else:
                            excluded_page_records.append(check_result)
                        progress_bar.progress(
                            int(index / len(unique_crawl_urls) * 100)
                        )

                progress_bar.empty()
                filtered_crawl_urls = prioritize_and_limit_urls(
                    [record.url for record in valid_page_records],
                    max_urls=int(crawl_limit),
                    prioritize_sitemap=prioritize_sitemap,
                )
                valid_record_map = {record.url: record for record in valid_page_records}
                filtered_valid_records = [
                    valid_record_map[url]
                    for url in filtered_crawl_urls
                    if url in valid_record_map
                ]
                st.session_state["crawl_results"] = results
                st.session_state["crawl_urls"] = filtered_crawl_urls
                st.session_state["selected_crawl_urls"] = list(filtered_crawl_urls)
                st.session_state["crawl_duplicates_removed"] = removed_duplicates
                st.session_state["crawl_total_before_limit"] = len(unique_crawl_urls)
                st.session_state["crawl_valid_before_limit"] = len(valid_page_records)
                st.session_state["crawl_limit_applied"] = int(crawl_limit)
                st.session_state["crawl_prioritize_sitemap"] = prioritize_sitemap
                st.session_state["crawl_start_path"] = start_path.strip() or "/"
                st.session_state["crawl_valid_records"] = [
                    asdict(record) for record in filtered_valid_records
                ]
                st.session_state["crawl_excluded_records"] = [
                    asdict(record) for record in excluded_page_records
                ]

        crawl_results = st.session_state.get("crawl_results")
        crawl_urls = st.session_state.get("crawl_urls", [])
        crawl_duplicates_removed = st.session_state.get(
            "crawl_duplicates_removed",
            0,
        )
        crawl_total_before_limit = st.session_state.get(
            "crawl_total_before_limit",
            len(crawl_urls),
        )
        crawl_limit_applied = st.session_state.get("crawl_limit_applied", 100)
        crawl_prioritize_sitemap = st.session_state.get(
            "crawl_prioritize_sitemap",
            True,
        )
        crawl_start_path = st.session_state.get("crawl_start_path", "/")
        crawl_valid_before_limit = st.session_state.get(
            "crawl_valid_before_limit",
            len(crawl_urls),
        )
        crawl_valid_records = st.session_state.get("crawl_valid_records", [])
        crawl_excluded_records = st.session_state.get("crawl_excluded_records", [])

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
            st.caption(f"Removed duplicates: {crawl_duplicates_removed}")
            st.caption(
                f"Start page: {crawl_start_path} | "
                f"Before limit: {crawl_total_before_limit} | "
                f"Valid after checks: {crawl_valid_before_limit} | "
                f"Limit applied: {crawl_limit_applied}"
            )
            if crawl_prioritize_sitemap:
                st.caption(
                    "Sitemap Page priority is enabled. Matching URLs are kept first."
                )

            action_col_1, action_col_2, action_col_3 = st.columns(3)
            with action_col_1:
                st.button(
                    "Select all",
                    on_click=set_multiselect_selection,
                    args=("all",),
                )
            with action_col_2:
                st.button(
                    "Clear all",
                    on_click=set_multiselect_selection,
                    args=("none",),
                )
            with action_col_3:
                st.button(
                    "Invert selection",
                    on_click=set_multiselect_selection,
                    args=("invert",),
                )

            selected_urls = st.multiselect(
                "Choose pages to send",
                options=crawl_urls,
                default=st.session_state.get("selected_crawl_urls", crawl_urls),
                key="selected_crawl_urls",
                help="You can select one, many, or all pages from the collected list.",
            )

            st.caption(f"Selected now: {len(selected_urls)}")
            sitemap_records = [
                record for record in crawl_valid_records if record["is_sitemap"]
            ]
            other_records = [
                record for record in crawl_valid_records if not record["is_sitemap"]
            ]

            sitemap_col, other_col = st.columns(2)
            with sitemap_col:
                st.markdown("#### Sitemap pages")
                st.text_area(
                    "Sitemap pages list",
                    value="\n".join(
                        f'[{record["status_code"]}] {record["url"]}'
                        for record in sitemap_records
                    ),
                    height=260,
                    disabled=True,
                )
            with other_col:
                st.markdown("#### Other pages")
                st.text_area(
                    "Other pages list",
                    value="\n".join(
                        f'[{record["status_code"]}] {record["url"]}'
                        for record in other_records
                    ),
                    height=260,
                    disabled=True,
                )

            with st.expander(f"Excluded pages: {len(crawl_excluded_records)}"):
                st.text_area(
                    "Excluded pages with reasons",
                    value="\n".join(
                        f'[{record["status_code"]}] {record["url"]} -> {record["reason"]}'
                        for record in crawl_excluded_records
                    ),
                    height=220,
                    disabled=True,
                )

            if st.button("Send collected URLs to OmegaIndexer", type="primary"):
                if not api_key:
                    st.error("Enter your OmegaIndexer API key in the sidebar.")
                elif not crawl_urls:
                    st.error("No URLs were collected yet.")
                elif not selected_urls:
                    st.error("Select at least one page.")
                else:
                    payload = OmegaCampaignPayload(
                        apikey=api_key,
                        campaignname=(
                            crawl_campaign_name.strip()
                            or "Homepage crawl campaign"
                        ),
                        urls=build_pipe_delimited_urls(selected_urls),
                        dripfeed=str(dripfeed_days),
                    )
                    success, message = send_to_omega(payload)
                    if success:
                        st.success("Collected URLs sent successfully.")
                        st.caption(
                            f"Unique URLs sent: {len(selected_urls)}. "
                            f"Removed duplicates: {crawl_duplicates_removed}."
                        )
                        st.code(message)
                    else:
                        st.error("OmegaIndexer rejected the request.")
                        st.code(message)


if __name__ == "__main__":
    main()
