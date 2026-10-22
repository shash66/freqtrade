# Repository notes (shash66/freqtrade fork)

This fork of freqtrade carries two independent pieces of work, on separate branches:

| Work | Branch | Entry point |
|---|---|---|
| Micro-trading paper bot on Kraken (freqtrade dry-run, strategy search, buy-and-hold comparison) | `claude/happy-fermi-w19gou` | `docs/micro-trading-research.md`, `user_data/strategies/WF_*.py` |
| **FlashLiquidator Lab**: simulation-only Aave V3 flash-loan liquidation research platform (Base, Arbitrum) | `flashliquidator-lab` | `flashliquidator-lab/README.md`, `flashliquidator-lab/CLAUDE.md`, `flashliquidator-lab/docs/runbook.md` |

Everything FlashLiquidator-related lives under `flashliquidator-lab/`; the rest of the tree is upstream
freqtrade. Do not mix the two. The lab has no live-trading path by design and must keep none.

To move the lab into its own repository: `git subtree split --prefix=flashliquidator-lab -b flashliquidator-lab-standalone`
then push that branch to the new repository's `main` (the standalone branch is also kept on this remote).
