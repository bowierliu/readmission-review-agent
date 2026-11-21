# Running a live portfolio investigation

The public demo runs the included synthetic replay without credentials or inference charges. The local launcher can also run an actual OpenAI investigation when its operator enables spending. On a recruiter's machine, it uses the recruiter's account. No key is requested by a website. A hosted owner-funded model endpoint would remove local setup, but would also need authenticated access, an operator, and a single durable accounting service. The supplied live servers bind only to loopback.

## One-time setup

1. Download the repository and follow its README to launch the local app. On macOS, put a generic application password in the login keychain using **Keychain Access → File → New Password Item**. Use item name **Healthcare Portfolio**, account **openai**, and your OpenAI project API key as the password. Use a dedicated standard global OpenAI project with the pinned model available. The key must permit the Responses and input-token-count endpoints. On other systems, provision **PORTFOLIO_OPENAI_KEY** in the local server's environment using your credential manager. The application reads the value at request time. Avoid putting keys into a command line or a repository file.
2. From any one of the three checkouts, run the commands below once for this OS account with Python 3.10 or later. All three launchers use the same ledger. New ledgers start disabled. Enabling can incur charges when someone clicks the local live action.

   ```sh
   python3 portfolio_guard.py init
   python3 portfolio_guard.py status
   python3 portfolio_guard.py enable
   ```

3. Use the README's launcher and select the **live** option in the local app. Only the local server submits the preset question, synthetic evidence and instructions to the fixed HTTPS host api.openai.com. The key is passed to that host in its authorization header. The client requests no response storage. OpenAI's own account and abuse-monitoring policies apply. The local ledger stores neither prompts nor responses. The apps never send a key to GitHub Pages.
4. To stop new model work across all three launchers, run `python3 portfolio_guard.py disable`. Already dispatched requests may complete and remain accounted. The public replay works independently.

### Save the key with a hidden macOS prompt

Open Terminal once and run the command below exactly as written. macOS prompts privately because `-w` is the final option. It stores a generic password in the default keychain. An existing matching item is preserved. All three launchers then read the same item each time you use live mode.

```sh
/usr/bin/security add-generic-password -s 'Healthcare Portfolio' -a openai -w
```

To check the name and account without displaying the key, run `/usr/bin/security find-generic-password -s 'Healthcare Portfolio' -a openai >/dev/null`. A successful lookup exits silently.

## Limits shared by the three local launchers

| Control name | Default | Scope |
| --- | ---: | --- |
| input_tokens | 4,096 | Exact provider input count per generation, including instructions |
| output_tokens | 384 | Maximum generated tokens per generation, including any non-visible tokens |
| model_calls | 8 | Generation reservations per investigation; failed attempts also count |
| tool_calls | 16 | Local tool dispatches per investigation |
| seconds | 90 | Investigation deadline; the provider subprocess has the remaining wall time |
| run_cents | 3 | USD cents per investigation |
| user_cents | 10 | USD cents per locally identified operator, lifetime of ledger |
| global_cents | 25 | USD cents across all operators and all three apps, lifetime of ledger |
| starts_per_minute | 6 | Investigation starts in the preceding 60 seconds, all apps combined |
| concurrency | 2 | Simultaneous investigations across all apps |

Change a limit with `python3 portfolio_guard.py set CONTROL POSITIVE_INTEGER`. For example, `python3 portfolio_guard.py set global_cents 12` sets the shared ceiling to $0.12. Use `status` to see the current settings and the total charged or held. There is no automatic reset. Lowering a ceiling can immediately prevent new reservations. It cannot cancel spending already authorized.

## Accounting and failure behavior

The fixed model is **gpt-5.4-mini-2026-03-17** at standard global text prices of **$0.75 per million input tokens** and **$4.50 per million output tokens**, checked November 21, 2026. The application sends plain text with no hosted tools, regional routing, service-tier upgrades, streaming, or automatic retries. A local MCP or SQL check does not incur a provider tool charge. [Model pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini) · [Exact input token counting](https://developers.openai.com/api/docs/guides/token-counting)

Each call first reserves its worst-case cost in a durable SQLite transaction, including the entire input and output allowances. At the defaults this is **$0.004800**. Concurrent callers contend for the same balance. The server counts the exact input with OpenAI before sending a generation. It rejects an oversized or uncountable request. A valid final response releases the unused reservation at full uncached prices. A timeout, unknown usage, missing response or process crash keeps the full reservation. Another click makes a new reservation. A mismatch in the returned model, tier or token bounds disables further calls. The ledger can overstate the provider bill after failed calls.

The fixed local identity is the OS account running the loopback server. All browsers share it; cookies, IP addresses and browser-supplied IDs have no effect. The SQLite database in **~/.healthcare-portfolio/ledger.sqlite3** is shared by all three projects on one machine and OS account. HTTP clients cannot edit prices, limits, keys or accounting. Use the same OS account for all demos using the same key. Keep the ledger on a durable local disk. Reinitializing after data loss, restoring a stale copy, running under another account/machine or independently using the key creates spending outside this ledger.

If the service crashes with an open run, its slot stays occupied. Stop all three servers, run `disable`, then `recover` using the same operator script. Recovery clears stuck concurrency slots and preserves all charges and reservations. Start the local app and re-enable only when ready. A missing ledger or an unavailable accounting database blocks model access.

These ceilings apply to generation requests made through these launchers at the verified standard prices. Check pricing and model availability before enabling the key. Taxes, external key use, provider-side pricing or billing changes, and provider requests already in flight are outside the application's control. A provider budget alert cannot enforce this ledger's ceiling. For a future multi-host deployment, use one central reservation service and a trusted authenticated identity before provisioning an owner key.

## Publish the static demo

From a reviewed checkout that matches the published main commit, run `sh scripts/publish-pages.sh`. This pushes only the browser assets named in the script to the `gh-pages` branch and preserves prior releases. Once per public repository, select **Settings → Pages → Deploy from a branch → gh-pages → / (root)**. Subsequent releases use the same script. The build requires no API key, database or backend.

## Reproduce the budget verification

Run `python3 scripts/verify_budget.py` from this checkout. It exercises 128 short sequences against the production ledger and kills a subprocess during a mocked call. Run `python3 -m unittest discover -s tests -p test_portfolio_guard.py` for concurrent callers, policy limits and the disable switch. Both commands use disposable local ledgers and make zero provider requests over the network.
