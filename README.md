# Dynamic Market Exchange — essential guide

Build an agent that bids for materials, assembles products, and sets sale
prices. Test it against the built-in agents, then upload it through the page at
the link provided by the organizers to compete with other teams.

Start with **section 1** to get a working run. **Section 2** explains the game
rules. Use **section 3** as a reference when changing an experiment; you do not
need to read every option before starting.

- [1. Get started](#1-get-started)
  - [1.1 Fork the repository](#11-fork-the-repository)
  - [1.2 Choose your workspace](#12-choose-your-workspace)
  - [1.3 Create your agent](#13-create-your-agent)
  - [1.4 Check that it runs](#14-check-that-it-runs)
  - [1.5 Submit your agent](#15-submit-your-agent)
- [2. Simulation mechanics](#2-simulation-mechanics)
- [3. Testing reference](#3-testing-reference)
  - [3.1 Options and configurations](#31-options-and-configurations)
  - [3.2 Using the UI](#32-using-the-ui)
  - [3.3 Comparing seeds and scenarios](#33-comparing-seeds-and-scenarios)
  - [3.4 Results and recordings](#34-results-and-recordings)
- [4. Troubleshooting](#4-troubleshooting)

## 1. Get started

### 1.1 Fork the repository

Sign in to GitHub, open the
[DME repository](https://github.com/AIRIC-Polimi/pricing-hackaton-test), and select
**Fork → Create fork** under your account. Use your fork for either workspace
below. Its address should be `YOUR-USERNAME/pricing-hackaton-test`.

### 1.2 Choose your workspace

Choose **one of these two alternatives**. Both use your fork and lead to the
same project; you do not need to complete both setups.

#### Option A — Codespaces: work in your browser

Use this option to work without installing Git, uv, or Python on your computer.
On your fork, select **Code → Codespaces → Create codespace on main**. Wait
for setup to finish, then open this guide in the editor and use the integrated
terminal. Python 3.12, uv, and project dependencies are installed automatically.
Stop the Codespace when you finish.

**Once setup finishes, skip Option B and continue to section 1.3.**

#### Option B — Local clone: work on your computer

Use this option if you prefer your own editor and terminal. Install Git and uv
using the expandable instructions below if needed. Replace `YOUR-USERNAME`
with the owner of your fork, then run:

```bash
git clone https://github.com/YOUR-USERNAME/pricing-hackaton-test.git
cd pricing-hackaton-test
uv sync --locked --python 3.12
```

Python 3.11–3.13 is supported; uv can download Python 3.12. The current runner
uses Unix-only timeout functions, so native Windows Python cannot run agent
decisions correctly. Windows users can use WSL, Codespaces, or a Linux container.

<details>
<summary>Need Git or uv? Installation commands by operating system</summary>

**Ubuntu / Debian** — install Git and download tools, then run uv's installer:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**macOS** — install Apple's command-line tools for Git, finish the installation
dialog, then install uv. Skip tools that are already installed:

```bash
xcode-select --install
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows with WSL** — if needed, install Ubuntu from PowerShell as Administrator:

```powershell
wsl --install -d Ubuntu
```

Restart if prompted, open Ubuntu, create its Linux account, and run the Ubuntu
commands above there. Keep the clone and simulation commands in the Ubuntu
terminal.

After installing tools, reopen the terminal and check `git --version` and
`uv --version`. For other Linux distributions or installation methods, see the
official [Git](https://git-scm.com/install/) and
[uv](https://docs.astral.sh/uv/getting-started/installation/) instructions.

</details>

**After either option**, continue to section 1.3. Run all subsequent commands
from the repository root: the `pricing-hackaton-test/` folder containing
`pyproject.toml`, `config/`, and `src/`.

### 1.3 Create your agent

Create a subfolder for each agent under
**`pricing-hackaton-test/src/dme_simulator/agents/`**. Put that agent's
`agent.py`, optional Python helpers, and optional models inside its folder:

```text
pricing-hackaton-test/                 # run commands here
  essential_README.md
  pyproject.toml
  config/
  src/
    dme_simulator/
      agents/
        base.py                       # leave existing simulator files unchanged
        loader.py
        builtin_bundles/              # supplied opponents; leave unchanged
        your-team-name/               # create one folder per agent
          agent.py
          strategy.py                 # optional
          models/                     # optional
```

Create the folder in the Explorer or with
`mkdir -p src/dme_simulator/agents/your-team-name`.
The simulator discovers agents here automatically, including all experimental
agent folders you leave in this directory. Put this starter in `agent.py`:

```python
from dme_simulator.agents import (
    AssemblyOrder, BaseAgent, Bid, Decision, Listing, Observation,
)
from dme_simulator.config import GoodsConfig


class StarterAgent(BaseAgent):
    def decide(self, state: Observation, goods_config: GoodsConfig) -> Decision:
        return Decision(
            bids=[
                Bid(lot.good, 1, lot.reserve_price * 1.1)
                for lot in state.auction_lots
            ],
            assembly=[
                AssemblyOrder(name, 1) for name in goods_config.recipes
            ],
            listings=[
                Listing(good, 1_000, 250.0)
                for good in goods_config.finished_goods
            ],
        )
```

The folder name is your agent ID. Define exactly one concrete `BaseAgent`
subclass in `agent.py`, constructible without arguments. Use only the supplied
public inputs, your own memory, and bundled models; treat inputs as read-only.
The [agent interface reference](docs/agent_interface.md) explains the inputs and
actions. The starter checks the setup; you should improve its strategy.

### 1.4 Check that it runs

From the repository root, first check syntax and discovery. Replace
`your-team-name` in the syntax-check path with your agent's folder name:

```bash
uv run python -m compileall -q src/dme_simulator/agents/your-team-name
uv run dme-sim --list-agents
```

Discovery should list your folder ID alongside the built-in agents. It imports
agent modules but does not construct agents or run turns.

Then run a short simulation:

```bash
uv run dme-sim --turns 5
```

This uses the default scenario and automatically loads your agents alongside
`DummyAgent`, `PatientAgent`, and `PriceWarAgent`. With one custom agent, you
should see **four teams**. The run prints capital and stock through turn 5 and
saves three dashboard PNGs in `outputs/default/`. No agent-path argument is
needed. If an agent takes no actions, add `--ui none` to expose exception and
timeout messages: a completed local run can still contain empty fallback decisions.

Now edit your strategy and rerun. Use the [testing reference](#3-testing-reference)
when you want the window UI, a longer run, or comparisons across seeds.

### 1.5 Submit your agent

Open the page at the **link provided by the organizers**, log in, and upload
your tested bundle as a folder or a single ZIP. The URL will be announced;
check validation status and official results on that page. Only the latest
submission before the deadline counts for scoring. Git commits and pull
requests to this repository are not submissions.

**Official scoring:** each match ranks agents by their final capital (cash),
highest first. Inventory adds no value to the score. Overall standings use the
arithmetic mean of each agent's ranks across its official matches; a lower
average rank is better. For example, ranks 1, 2, and 3 give an average rank of
2.

Before uploading, check:

- Exactly one `agent.py`, with one concrete agent class callable without
  arguments; optional Python helpers and supported model files stay inside
  the same bundle.
- At most **64 files** and **20 MiB of file contents**. A ZIP must also be at
  most 20 MiB. Upload only allowed Python/model files; exclude caches, plots,
  documentation, and virtual environments.
- All Python files compile, including unused helpers. Load models once in
  the constructor and use dependencies available in the evaluator; uploads
  cannot install packages.
- Portal validation tests three default-scenario turns. Decisions have a
  **10 ms** deadline; validation has a **10-second** wall-clock deadline and a
  CPU allowance. Errors or timeouts reject the upload.

Local scores are experiment results, not official competition scores. In
Codespaces, download your bundle files for the browser's upload picker. Review
the file rules below before your first upload.

<details>
<summary>Submission file rules and model loading</summary>

- **Bundle layout:** exactly one `agent.py` in the submission. An enclosing
  upload folder is allowed, but all files must be inside the folder containing
  that entry point. Optional helpers may define functions or classes; imported
  classes do not count as additional agent entry points. Every Python file
  must be valid UTF-8 Python.
- **ZIPs:** upload one `.zip` alone, using stored or deflate compression without
  encryption. The 64-file and 20 MiB contents limits apply after extraction;
  the ZIP itself must also be at most 20 MiB (20,971,520 bytes). Directory
  entries do not count as files. Encrypted or corrupted archives, links,
  special files, unsafe or duplicate entries, and nested archives are rejected.
  Selecting a folder or ZIP in the browser clears the other selection.
- **Allowed extensions:** `.py` and optional model files with lowercase
  extensions `.bin`, `.pt`, `.pth`, `.pkl`, `.pickle`, `.joblib`, `.onnx`,
  `.safetensors`, `.npy`, `.npz`, `.h5`, or `.hdf5`. Other extensions and
  hidden files are rejected. Exclude `__pycache__/`, `.pyc`, documentation,
  plots, `.git/`, and `.venv/` from uploads.
- **Paths:** each file or folder name must begin with a letter, number, or
  underscore; the remaining characters may also include dots, spaces, and
  hyphens. Relative paths must be at most 240 characters. Subfolders are
  preserved; absolute paths, traversal paths such as `../`, and duplicate
  paths are rejected.
- **Models:** the evaluator provides Pickle, NumPy, and CPU PyTorch loaders.
  Other formats need a compatible loader from its installed dependencies;
  accepting an extension does not install a runtime. Pickled models depending
  on unavailable packages fail to load. Model bytes are stored unchanged.
  The local simulator's locked environment does not include PyTorch.
- **Loading:** resolve model files with `__file__`, for example
  `Path(__file__).parent / "models" / "weights.npz"`, and load them once in
  your constructor. In the evaluator, import, construction, and decisions run
  with the Python file's directory as the working directory. Validation and
  tournament play have the same paths and installed dependencies. Uploads
  cannot supply requirements files or install packages.
- **Validation:** the browser checks folder limits, paths, layout, extensions,
  and Python UTF-8; for ZIPs it checks the filename and compressed size, with
  extraction and contents validation on the server. The server compiles every
  Python file, including unimported helpers, then imports a disposable copy,
  constructs the agent, and checks three default-scenario decisions. Invalid
  actions, wrong return types, exceptions, missing files/dependencies, and
  timeouts reject the upload. Passing this smoke check does not guarantee
  valid decisions in every competition scenario.

For local helpers, use relative imports such as `from .strategy import choose`.
A bundle does not need an `__init__.py`; if present, that file is not executed
by the bundle loader. Your agent may use its own models and memory, public
observations, and goods configuration. It must not inspect simulator internals
or other submissions.

</details>

## 2. Simulation mechanics

In this section there is a complete overview of the simulator mechanics.

There are no workforce costs and no storage costs. Capital only leaves your
company through material purchases at auction and only returns through sales.
Unsold raw materials and finished goods carry over to the next turn for free.

### 2.1 The turn cycle

At the start of each turn, every solvent agent receives an `Observation` and has
`agents.decide_timeout_ms` milliseconds to return one `Decision` containing its
bids, assembly orders, and sales listings. The agent chooses all three before
the auction closes: it cannot see which bids won and then revise what to
produce or list. The simulator applies that same decision in three phases:

1. **Auction** — the published raw-material lots are sold to the highest sealed
   bids; winners' materials land in their inventory.
2. **Production** — teams convert raw materials into finished goods by recipe.
3. **Sales** — teams list finished goods at their chosen prices and spawned
   customers buy them; revenue is paid immediately.

The later phases use the materials and finished goods actually available when they run, so a planned assembly or listing may be only partly fulfilled. Agents also choose without seeing the other teams' current decisions. Random behavior is reproducible for a given configuration and seed.

### 2.2 Phase 1 - Raw-material auction

Every turn raw-material suppliers replenish their stock by a quantity drawn from
the scenario's configured range. Unsold stock carries over. The simulator
publishes the available stock as a lot with a dynamic `reserve_price`; it starts
from the configured `base_price` and falls as supplier stock accumulates. The
lots are public and visible in `state.auction_lots` before you bid.

Each `suppliers.<good>` entry has its own `price_stock_factor`,
`price_floor_multiplier`, and `adaptation` settings. These control the
reserve-price curve and whether persistent empty or full stock changes that
supplier's replenishment and stock capacity.

A `Bid(good, quantity, price_per_unit)` is sealed: no team sees another team's
bid. For each material, bids are sorted by price, highest first, and filled
until the lot runs out, so a bid may be filled partially or not at all. Bids
below the reserve price are discarded.

**Pay-your-bid.** A winner pays its own bid price per unit, not the lowest
winning price. Bidding 100 when the lot would have cleared at 30 costs you 100.

**Ties.** Bids at exactly the same price share the remaining units through a
round robin in random team order.

**The budget cap.** Your winnings are capped by the capital you still hold: the
simulator tracks your remaining capital across the auction and never allocates
more units than you can pay for. Materials clear in a random order each turn, so
no material systematically gets first claim on your budget. Bidding for more
than you can afford is safe for your solvency, but it costs you the surplus
units — you cannot predict which materials your budget will be spent on.

### 2.3 Phase 2 - Production

Recipes convert raw materials into finished goods. Each recipe defines its
output good and the quantity of each input material. Assembly orders are
processed in the order returned by the agent and are limited only by the
materials you hold, plus the scenario's optional `production.capacity_per_turn`
cap on total units per team per turn. Inputs are consumed and finished goods are
available to sell in the same turn.

### 2.4 Phase 3 - Sales

A `Listing(good, quantity, price)` offers a finished good for sale; `good` must
be in `goods_config.finished_goods`. You may list any amount, including none,
and you may split one finished good across several listings at
different prices. A listing is capped by the inventory you actually hold, and
only a positive price makes it available to buyers.

Unsold and unlisted stock simply stays in inventory for the next turn at no
cost.

Customers are spawned per turn, in a quantity that fluctuates over time. Each
customer belongs to a **segment** — a customer type with its own share of the
crowd, its own preferences over goods, and its own willingness to pay. A budget
segment that mostly wants smartwatches will not pay laptop prices, and a premium
segment will.

Segment definitions are simulator configuration, not agent input. Each customer
picks a good by its segment's preferences and draws a private valuation from
that segment's range for that good. It then buys from the listings priced at or
below that valuation; lower prices are more likely to win the sale, but
customers do not always choose the cheapest eligible seller. A customer who
finds no affordable listing leaves without buying.

### 2.5 Public information

Inventories are public. `state.public_inventories` and `state.public_capital`
show every team's holdings and capital, so you can see who is stockpiling and
who is short. `state.auction_transactions` and `state.sales_transactions`
contain completed transactions, including the good,
quantity, unit price and buyer for auctions. Sales records include the seller
and omit the buyer because customers are anonymous. These lists accumulate
throughout the run. By default they contain only transactions involving the
observing team; set
`agents.observe_all_transactions` to `true` to expose the whole market log.

What you do not see is the current turn's bids and listings, which are sealed
until the turn resolves.

### 2.6 Bankruptcy

If capital is below zero at the end of a turn, the company is liquidated: its
inventory is cleared and it takes no further turns. Because the auction's budget
cap prevents overspending, bankruptcy is rare and generally means selling below
cost.

### 2.7 Configuration and analytics

Market scenarios are YAML files under `config/`. They define the duration and
seed, initial capital, per-material supplier production and pricing policies,
the production cap, customer segments, goods and recipes, the agent roster,
transaction visibility, and output settings. Unknown configuration keys and
invalid cross-references fail fast.

The included sample scenarios are:

- `config/tutorial_pricing.yaml`: one good, materials abundant and nearly free —
  only the listed price decides the winner;
- `config/tutorial_auction.yaml`: one good that always sells at about the same
  price — only what you pay at auction decides the winner;
- `config/default.yaml`: the standard hardware market, four materials, five
  goods, three customer segments;
- `config/scaled_market.yaml`: a larger, busier market with scarcer materials.

The tutorials are meant to be used in that order: isolate pricing, then isolate
bidding, then handle both together in the full market.

`initial_state.initial_inventory` gives every team a private copy of the
configured starting stock. Goods not listed there start at zero.

## 3. Testing reference

### 3.1 Options and configurations

Add or change options on the command in [section 1.4](#14-check-that-it-runs).
Omit `--turns 5` to use a scenario's full duration. Scenarios are described in
[section 2.7](#27-configuration-and-analytics); keep the official files unchanged
when comparing agents.

| Option | Purpose |
| --- | --- |
| `--config PATH` | Choose a YAML scenario; defaults to `config/default.yaml`. Repeat for several scenarios. |
| `--turns N` | Override duration with a positive turn count. |
| `--seed N` | Set a reproducible seed; otherwise use the scenario's seed. |
| `--ui window`, `terminal`, or `none` | Choose a pygame window, live console dashboard, or plain progress. |
| `--turn-delay SECONDS` | Pace a live display; nonnegative seconds per turn. |
| `--output-dir PATH` | Choose the directory for PNG charts. |
| `--simulations N` | Run N consecutive seeds per scenario; defaults to one. |
| `--max-workers N` | Limit parallel batch workers; defaults to CPU count. |
| `--list-agents` | Print discovered bundle IDs without constructing agents or running turns. Agent modules are imported. |
| `--profile` | Print decision and phase timings for one simulation. Cannot be used for batches. |
| `--guide` | Print the dashboard guide and exit. |
| `--record [PATH]` | Record a single run; see section 3.4. |
| `--replay PATH` | View a recording without loading agents or running a simulation. |
| `--help` | Show the complete command-line help. |

CLI paths start from your working directory. All supplied scenarios discover
bundles under `src/dme_simulator/agents` recursively. Each bundle folder name
must be unique. With the supplied scenarios, every discovered bundle competes
and no path arguments are needed. For a different roster, `--agents-dir PATH`
replaces the search root (repeat for several roots); `--extra-agent PATH` adds
a bundle stored elsewhere. Do not append a bundle already in the default root.
These overrides are optional; `uv run dme-sim --help` describes their syntax.

### 3.2 Using the UI

For a graphical run, add `--ui window --turn-delay 0.5` to the smoke command. It starts paused at turn 0. Press **Space** or click **Play**
to begin. On a terminal, use `--ui terminal`; add a delay to follow each turn.
Redirected terminal output falls back to plain progress. `--ui none` runs
without a live dashboard. Batch runs skip the live UI.

| Window control | Action |
| --- | --- |
| Space / Play | Pause or resume. |
| Right / N / Next | View or execute the next turn. |
| Left / P / Back | Pause and view the previous turn. |
| Home / Restart | Return the viewer to turn 0 and pause; preserves simulation state. |
| Up / Down | Speed up / slow down. |
| C / chart tabs | Switch between Cash, Cash flow, and Sales charts. |
| H / F1 / Guide | Open the guide with metric definitions and controls. |
| Ctrl + plus/minus; Ctrl + 0 | Zoom; reset zoom. |
| Mouse wheel / Page Up / Page Down | Scroll the panel under the pointer or the guide. |
| Esc | Close the guide if open; otherwise stop the run. |
| Q / close window | Stop the run; close the viewer after completion. |

Browsing history never reruns decisions. Play catches up through saved turns
before continuing live execution. At completion, the window remains open for
inspection. Cash excludes inventory value; cash flow is sales revenue minus
auction spending in that turn. Use the guide for the full dashboard legend.

**Codespaces:** the provided devcontainer supports terminal/headless runs. A
graphical UI is possible with an added browser desktop such as
[desktop-lite/noVNC](https://github.com/devcontainers/features/tree/main/src/desktop-lite),
which is not included in the current configuration. You can also download a
recording and replay it locally. Press H/F1 in the viewer for the scrollable
guide, or run `uv run dme-sim --guide` to read it in the terminal.

### 3.3 Comparing seeds and scenarios

Before choosing a strategy, compare it across multiple seeds and both full
market scenarios. For example:

```bash
uv run dme-sim \
  --config config/default.yaml --config config/scaled_market.yaml \
  --simulations 3 --turns 30 --seed 100 --max-workers 3 \
  --output-dir outputs/comparison
```

This runs seeds 100, 101, and 102 for each scenario: six simulations with up to
three workers. Fresh agents are constructed for each run. Outputs aggregate
each scenario into mean curves with standard-error bands. Keep team IDs and
roster order consistent when comparing seeded results. Use `--profile` on a
single run to inspect decision timing against the 10 ms default deadline.

### 3.4 Results and recordings

With plot rendering enabled in the scenario, the chosen output directory gets:

| PNG suffix | What to inspect |
| --- | --- |
| `*_dashboard_overview.png` | Capital, cash movements, market share, and captured/missed demand. |
| `*_dashboard_auction.png` | Reserve and winning prices, offered materials, fill rates, and units won. |
| `*_dashboard_sales.png` | Units sold, listed/realized prices, unserved customers, and unsold stock. |

To save a run for later viewing, add bare `--record` to a single-run command.
It creates `outputs/recordings/<timestamp>/simulation.jsonl` and prints a
copyable replay command. `--output-dir` controls plots, not this recording path.
Alternatively, pass an explicit file with an existing parent directory;
recording refuses to overwrite files. Batches cannot be recorded.

Run the printed `uv run dme-sim --replay PATH` command on a graphical desktop.
Replay needs neither your agent nor the original scenario, produces no new
plots, and uses the same window controls. It accepts only `--ui window` and
`--turn-delay` alongside `--replay`. Recordings are spectator data; your agent
must not read them.

## 4. Troubleshooting

| Problem | Check |
| --- | --- |
| Tool not found | Reopen the terminal after installation; check `git --version` and `uv --version`. |
| Unsupported Python | Use the Python 3.12 sync command in section 1.2. |
| Codespace disconnects during rebuild | Reconnect after the container restarts and setup finishes. |
| Agent fails to load | The specified folder must contain `agent.py`, defining exactly one concrete agent callable without arguments. |
| Built-in opponents missing | Use the supplied scenarios without a custom `--agents-dir`; place your agents under the default root in section 1.3. |
| Duplicate ID | Give each agent bundle a unique folder name; the Python class name does not set its ID. |
| Agent takes no actions | Add `--ui none` to show warnings and tracebacks. Exceptions, malformed decisions, and timeouts cause empty fallback decisions. |
| Window unavailable | Use the environment guidance in section 3.2. Native Windows needs a Unix environment for the current timeout implementation. |
| Upload rejected | Check every Python helper and the full submission rules; exclude caches and unaccepted file types. |
| Portal URL missing | Use the link supplied by the organizers; local tests are available before it opens. |
| Poor results | Inspect auction, sales, and inventory charts, then compare more seeds. A single local score is not an official ranking. |
