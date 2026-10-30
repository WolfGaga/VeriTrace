import multiprocessing
import json
import os
import time
import argparse
import platform
from tqdm import tqdm
from .api import scrape_url
from .io_utils import load_jsonl


def scrape(citation_url):
    retries = 0
    while retries < 3:
        result = scrape_url(citation_url)
        retries += 1
        if 'error' in result:
            time.sleep(1)
        else:
            break

    url_content = None
    if 'error' not in result:
        title = result.get('title', '')
        content = result.get('content', '')
        description = result.get('description', '')

        url_content = f"{title}\n\n{description}\n\n{content}"
    else:
        url_content = f"scrape failed: {result.get('error', 'unknown error')}"
    # print(f"url_content: {url_content}")
    return {
        'url': citation_url,
        'url_content': url_content
    } 


if __name__ == '__main__':
    if platform.system() == 'Darwin':  
        try:
            multiprocessing.set_start_method('spawn')
        except RuntimeError:
            pass
    else:
        try:
            pass  # 'fork' not available on Windows
        except RuntimeError:
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--output_path", type=str, required=True)
    parser.add_argument("--raw_data_path", type=str, required=True)
    parser.add_argument("--n_total_process", type=int, default=1,
                        help="Global concurrency for URL scraping across ALL articles (not per-article). "
                             "Flattens all unique URLs into one pool. Up to 50 recommended for Jina paid tier.")
    args = parser.parse_args()
    
    output_path = args.output_path
    
    # initialize variables
    raw_data = []
    data_to_process = []
    processed = []
    
    try:
        raw_data = load_jsonl(args.raw_data_path)
        
        if os.path.exists(output_path):
            # Load previously-written articles; an article is "fully done" only
            # when none of its citations are "scrape failed:" sentinels. Articles
            # with any failed URLs are re-queued so we can retry those URLs.
            existing = load_jsonl(output_path)
            fully_done_ids = set()
            partial_by_id = {}
            for d in existing:
                cits = d.get('citations_deduped', {}) or {}
                has_failed = False
                for v in cits.values():
                    c = v.get('url_content') if isinstance(v, dict) else None
                    if isinstance(c, str) and c.startswith('scrape failed:'):
                        has_failed = True
                        break
                if has_failed:
                    partial_by_id[d['id']] = d
                else:
                    fully_done_ids.add(d['id'])

            # For articles that are fully done, skip them. For partial articles,
            # use the previously-written record (which has up-to-date citations
            # and any successful url_content already filled) instead of raw.
            data_to_process = []
            for d in raw_data:
                if d['id'] in fully_done_ids:
                    continue
                if d['id'] in partial_by_id:
                    data_to_process.append(partial_by_id[d['id']])
                else:
                    data_to_process.append(d)

            # If any articles are partial-retry, rewrite the output file to drop
            # their old (failed) records so the append below produces clean output.
            if partial_by_id:
                kept = [d for d in existing if d['id'] in fully_done_ids]
                with open(output_path, 'w', encoding='utf-8') as f:
                    for d in kept:
                        f.write(json.dumps(d, ensure_ascii=False) + "\n")
                print(f"Retrying scrape-failed URLs in {len(partial_by_id)} previously-partial articles")
        else:
            data_to_process = raw_data
    except:
        import sys
        print(f"cannot process file {args.raw_data_path}")
        sys.exit(f'{args.raw_data_path} has not been processed yet...')
    
    print(f"processing {len(data_to_process)} instances...")

    # Flatten all URLs across all articles into one deduplicated set.
    # We also retry any URL whose previous url_content is a "scrape failed:" sentinel.
    # Jina cost is ~¥0.0175/call so retrying broken URLs is effectively free.
    def _needs_scrape(v):
        if 'url_content' not in v:
            return True
        c = v.get('url_content')
        if not c:
            return True
        if isinstance(c, str) and c.startswith('scrape failed:'):
            return True
        return False

    urls_to_scrape = set()
    for d in data_to_process:
        for url, v in d.get('citations_deduped', {}).items():
            if _needs_scrape(v):
                urls_to_scrape.add(url)
    urls_to_scrape = list(urls_to_scrape)

    print(f"scraping {len(urls_to_scrape)} unique URLs across {len(data_to_process)} articles "
          f"with global concurrency={args.n_total_process}...")

    # Run them through one global pool
    url_to_result = {}
    n_total_process = min(args.n_total_process, len(urls_to_scrape)) if urls_to_scrape else 0

    if n_total_process == 1:
        results = [scrape(u) for u in tqdm(urls_to_scrape, desc="scraping")]
    elif n_total_process > 1:
        try:
            with multiprocessing.Pool(processes=n_total_process) as pool:
                results = list(tqdm(pool.imap_unordered(scrape, urls_to_scrape),
                                    total=len(urls_to_scrape), desc="scraping"))
        except KeyboardInterrupt:
            print("scrape process interrupted")
            results = []
        except Exception as e:
            print(f"scrape process error: {e}")
            # if error, fall back to single process
            results = [scrape(u) for u in tqdm(urls_to_scrape, desc="scraping (fallback)")]
    else:
        results = []

    for res in results:
        url_to_result[res['url']] = res

    # Walk articles, merge results back, write out
    for d in tqdm(data_to_process, desc="writing"):
        for url, v in d.get('citations_deduped', {}).items():
            if _needs_scrape(v):
                res = url_to_result.get(url)
                if res is not None:
                    # Mirror existing merge: write url_content into the citation entry
                    d['citations_deduped'][url]['url_content'] = res['url_content']

        # write the updated data to the output file
        with open(output_path, 'a+', encoding='utf-8') as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
