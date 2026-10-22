export interface NavItem {
  href: string;
  label: string;
  short: string;
  description: string;
}

export const NAV: NavItem[] = [
  { href: "/", label: "Overview", short: "OV", description: "Network, position and simulation status" },
  { href: "/opportunities", label: "Opportunity Explorer", short: "OP", description: "Simulated liquidation candidates" },
  { href: "/backtests", label: "Historical Backtesting", short: "BT", description: "Replay strategies over past blocks" },
  { href: "/competition", label: "Competition Lab", short: "CL", description: "Model competing liquidators" },
  { href: "/simulator", label: "Flash-Loan Simulator", short: "FS", description: "Simulate a single liquidation" },
  { href: "/research", label: "Research", short: "RS", description: "Networks, protocols, observability and latency" },
  { href: "/strategies", label: "Strategies", short: "SG", description: "Multi-strategy profitability ranking (Phase 5)" },
  { href: "/settings", label: "Settings", short: "ST", description: "Read-only runtime configuration" },
];
