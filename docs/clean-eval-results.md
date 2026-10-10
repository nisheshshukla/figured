# Clean evaluation of figured 0.4.0: results

The protocol is in [clean-eval-protocol.md](clean-eval-protocol.md), committed and pushed (ab82929) before any of this data was run. The library was frozen at `v0.4.0`; `git diff v0.4.0 -- src` is empty for every number here. The evaluation script was committed (4d9a154) before its first run, and each set was run once. Nothing was changed after seeing results. Bugs found are listed below, not fixed. Output files are in `benchmarks/results/clean/`.

## Headline

**On new runs in domains like the ones figured was developed on, the development numbers held. On new domains they did not.** In ToolScale's banking, medicine, movie, e-commerce and basketball domains, 12.8% of successful runs carried a flag, against roughly 1% predicted, and a plain substring check did as well as figured. The causes are listed in the review below:

- date ranges figured does not derive
- two date formats it does not read
- one bug
- agents choosing search parameters

Every other prediction held within its range or close to it. The exception is the ambiguity check on airline tasks, which asked more often on good runs than on bad ones.

## Results against the predictions

| Measure | Predicted | A. ToolScale, new domains | B. tau2 airline and retail, new runs | B'. tau2 telecom, new models | C. AgentDojo, 4 new models |
|---|---|---|---|---|---|
| Successful runs flagged, figured / baseline | about 1% | **12.8%** (51 of 397) / 11.3% | 1.1% (19 of 1,755) / 14.9% | 21.4% (84 of 392) / 21.4% | |
| Failed runs flagged | | 35.2% / 36.7% | 4.6% / 23.0% | 25.8% / 25.2% | |
| Absent corruptions caught | about 99% | 99.4% / 99.6% | 98.1% / 90.4% | 99.8% / 99.8% | |
| Substitutions caught | about 0% | 0 of 421 | 2 of 3,019 | 0 of 947 | |
| Real errors in failed runs flagged | 2 to 4% | no ground truth | 2.7% (33 of 1,235) | 1.4% (6 of 444) | |
| `ambiguous_before` asks on writes, successful / failed runs | 2 to 25% | 2.2% / 32.2% | 9.6% / 14.1% (airline 37.0% / 23.8%) | | |
| Calls to tools the agent does not have | | 0 | 0 | 0 | |
| Injections that succeeded, flagged with rules | about 80% | | | | 86.3% (612 runs) |
| Benign completed runs flagged with rules | about 25% | | | | 23.6% (330 runs) |

Per file:

| Set | File | Successful runs flagged | Corruptions | Real errors |
|---|---|---|---|---|
| A | DeepSeek V4 Pro | 4.5% / 3.6% | 307 of 310 | |
| A | Qwen 3.6 Plus | 16.1% / 14.4% | 771 of 774 | |
| B | Claude 3.7 Sonnet airline | 2.0% / 24.0% | 251 of 277 | 3 of 174 |
| B | GPT-4.1 airline | 3.6% / 25.0% | 243 of 272 | 3 of 129 |
| B | o4-mini airline | 2.5% / 20.3% | 283 of 304 | 3 of 82 |
| B | GPT-4.1-mini airline | 1.0% / 25.7% | 261 of 274 | 13 of 219 |
| B | Claude 3.7 Sonnet retail | 0.0% / 10.0% | 1,075 of 1,077 | 0 of 141 |
| B | GPT-4.1 retail | 0.0% / 8.3% | 1,003 of 1,003 | 3 of 162 |
| B | o4-mini retail | 0.3% / 11.0% | 968 of 968 | 3 of 141 |
| B | GPT-4.1-mini retail | 2.7% / 19.9% | 896 of 899 | 5 of 187 |
| B' | o4-mini telecom | 0.5% / 0.5% | 561 of 562 | 5 of 366 |
| B' | GPT-4.1-mini telecom | 41.5% / 41.5% | 598 of 599 | 1 of 78 |

On ToolScale, DeepSeek's "failed" runs are those with an action match below 1.0 against a reference, not task failures. The 61% of them flagged, against 4.5% of its successful runs, is the largest gap between good and bad runs in this evaluation. The baseline shows the same gap.

AgentDojo per model, with source rules, injections that succeeded / benign runs flagged:

- GPT-4o: 87.3% / 25.6%
- GPT-4o-mini: 84.8% / 23.3%
- Claude 3.5 Sonnet (2024-10-22): 71.4% of 7 / 20.2%
- Gemini 2.0 Flash: 86.6% / 26.8%

Llama 3.3 70B's 1,027 runs could not be read: they use a newer AgentDojo message format, where content is a list of blocks keyed `content` instead of `text`. figured raised `UnrecognizedMessage` rather than reporting them clean, which is the designed behaviour, and they are excluded.

## Review of flags on successful runs

The protocol said to classify each flag as genuine or false. Reading them showed a third case, so the classes below were refined after seeing the flags:

1. **Made up, and it matters:** a value with no source sent to a tool.
2. **Invented legitimately:** a parameter the agent chose on a read call, or one the user asked it to choose.
3. **False flag:** the value does follow from the context, in a way figured does not model.

**Set B, all 23 unique flags:**

- **11 made up:**
  - placeholders: `credit_card_0000000` three times, `gift_card_0000000` twice, zip `00000`
  - the example item ID `1008292230` copied from the tool description, and the same ID plus one
  - `credit_card_7334` built from "the card ending in 7334"
  - `credit_card_7644869` where the real ID was `paypal_7644869`
  - a zip that lost its leading zero (`8321`)
- **2 invented:** search dates guessed from "sooner" and "the first week of the month".
- **10 false:** nine payment splits (a total minus a gift card's balance, or half a total rounded) and one date from "the 8th of this month".

**Set A, DeepSeek, all 7 unique:**

- **0 made up.**
- **2 invented with the user's approval:** a payment method ID the user told the agent to choose, and an expiry date "one year out" that the user confirmed.
- **5 false:**
  - three dates copied from tool results that write dates as `26-01-01`, a format figured does not read
  - two endpoints of a "yesterday" window, in prompts that never state today's date

**Set A, Qwen, a random 40 of 242:**

- **1 made up:** a year guessed wrong for "August 20th".
- **16 invented:** page sizes (`limit = 50`) and widened search windows or amount bounds around what the user said.
- **23 false:**
  - 17 month ranges ("August" → the 1st to the 31st, or an exclusive end on the 1st of the next month)
  - 2 year ranges ("2024" → January 1)
  - 2 timestamps hit by the bug below
  - 1 amount "50000" against "50,000"
  - 1 more month range

**Set B', GPT-4.1-mini telecom (not required by the protocol):** the flags are the agent calling `get_customer_by_name` with a date of birth it made up, `1970-01-01` or `0000-00-00`, in 41.5% of its successful runs. These are genuine. The baseline sees them too.

## What this shows

- **The low false-flag rate is a property of the domains figured was tuned on.** It holds on new models and runs in those domains (1.1% against the baseline's 14.9%). It does not hold where agents search by date range: ToolScale's banking runs are mostly "show my transactions from August". There figured flags as often as a substring check, and most of its flags are false or harmless.
- **Catching made-up values held everywhere:** 98 to 99.8% of corrupted values caught, and the made-up values it did flag were real fabrications. It still catches about 2% of real errors and none of the substitutions.
- **The ambiguity check is inconsistent.** It separated good from bad runs strongly on DeepSeek ToolScale (2.2% against 32.2% of writes), weakly on retail, and backwards on airline.
- **The AgentDojo source-rule results generalized** across four model families.

## Bugs and gaps found, not fixed here

- **ISO timestamps with seconds and a zone** (`2025-08-02T23:59:59Z`) are classified as identifiers, not dates, so a date the user stated is flagged. On ToolScale this caused 52 flags, and 6 of the 51 flagged successful runs had no other flag.
- **Dates written `YY-MM-DD`** (`26-01-01`) in tool results are not read.
- **Month, year, and relative ranges** are not derived: "August" → 08-01 and 08-31, "2024" → 01-01, "yesterday" without a reference date.
- **A numeric string of five or more digits** ("50000") is treated as an identifier, so it does not match "50,000".
- **Payment splits** (a total minus a balance) are not derived.
- **AgentDojo's newer transcript format** (content blocks keyed `content`) is not read.
- **Agent-chosen parameters on read calls** (page sizes, widened search windows) are flagged. Whether they should be is a policy question: they are not provenance errors.

Fixing these and measuring again on these sets would no longer be a clean measurement; the next clean number needs data figured has not seen.
