# OmegaIndexer Streamlit helper

## What this app does

- Sends URLs to OmegaIndexer over the public API.
- Collects internal links found on each site's homepage with a Googlebot user agent.
- Counts collected URLs and lets you submit them as a campaign.

## OmegaIndexer API fields

According to the integration page, the request body uses these string fields:

- `apikey`
- `campaignname`
- `urls`
- `dripfeed`

The `urls` field must contain pipe-delimited URLs such as:

`https://example.com/a|https://example.com/b`

The `dripfeed` field is in days and the public page says the maximum is `30`.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Documentation

- Russian wiki/instruction: [WIKI_RU.md](C:/Users/cas/Documents/GitHub/omegaindexer/WIKI_RU.md)
