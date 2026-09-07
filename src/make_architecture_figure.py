"""Generates the control-architecture block diagram used in the presentation."""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

FIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "figures")
os.makedirs(FIG, exist_ok=True)

BLUE, ORANGE, GREEN, GREY, RED = "#0B6FA4", "#E8820C", "#2E7D32", "#4A4A4A", "#C62828"


def box(ax, x, y, w, h, title, lines, fc, ec, fs=8.4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=fc, ec=ec, lw=1.8, zorder=2))
    ax.text(x + w / 2, y + h - 0.052, title, ha="center", va="top", fontsize=fs + 1.5,
            fontweight="bold", color=ec, zorder=3)
    ax.text(x + w / 2, y + h - 0.125, "\n".join(lines), ha="center", va="top",
            fontsize=fs, color="#222", zorder=3, linespacing=1.5)


def arrow(ax, p0, p1, text=None, color=GREY, rad=0.0, fs=8.0, dx=0.0, dy=0.03):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=15,
                                 lw=1.7, color=color, zorder=4,
                                 connectionstyle=f"arc3,rad={rad}"))
    if text:
        ax.text((p0[0] + p1[0]) / 2 + dx, (p0[1] + p1[1]) / 2 + dy, text, ha="center",
                va="bottom", fontsize=fs, color=color, fontweight="bold", zorder=5)


def main():
    fig, ax = plt.subplots(figsize=(13.2, 5.0))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    ax.text(0.5, 0.985, "Two-Layer Autonomous Choke Control Architecture",
            ha="center", va="top", fontsize=14, fontweight="bold", color="#111")

    box(ax, 0.015, 0.50, 0.185, 0.33, "OPERATOR",
        ["Requested oil rate", "target  Q_sp", "", "Safe operating", "envelope limits"],
        "#F2F6F9", GREY)

    box(ax, 0.235, 0.50, 0.245, 0.33, "LAYER 1 — SSTO",
        ["Scan choke 0–100 % on the", "identified gain curves",
         "Keep only feasible steady states", "Pick rate closest to target",
         "→ u_ss , Q_achievable , mode"], "#FFF4E5", ORANGE)

    box(ax, 0.515, 0.50, 0.265, 0.33, "LAYER 2 — DYNAMIC MPC",
        ["451 candidate 2-move plans (±5 %)", "Roll out over P = 40 h horizon",
         "REJECT any plan leaving envelope", "Minimise Σ(Q−Q_sp)² + w·Σ Δu²",
         "Apply first move only"], "#EAF3F8", BLUE)

    box(ax, 0.815, 0.50, 0.170, 0.33, "PLANT",
        ["Choke actuator", "(ramp limit ±5 %)", "", "Naturally flowing", "oil well"],
        "#EDF6ED", GREEN)

    box(ax, 0.235, 0.075, 0.265, 0.30, "CONTROL-ORIENTED MODEL",
        ["Hammerstein, identified from", "our own open-loop step tests",
         "y_ss(u) = a₀+a₁u+a₂u²", "τ dy/dt = y_ss(u) − y",
         "R² = 0.98–0.99 on unseen data"], "#F7F2FA", "#6A1B9A")

    box(ax, 0.540, 0.075, 0.240, 0.30, "DISTURBANCE ESTIMATOR",
        ["bias ← bias + K·(y_meas − ŷ)", "", "Constant output-disturbance",
         "(DMC) form → offset-free", "tracking under mismatch"], "#FDECEC", RED)

    arrow(ax, (0.200, 0.665), (0.235, 0.665), "target")
    arrow(ax, (0.480, 0.665), (0.515, 0.665), "u_ss, Q_sp")
    arrow(ax, (0.780, 0.665), (0.815, 0.665), "choke u")
    # measurement feedback: straight down the right margin, then left into the
    # disturbance estimator, so it never crosses another block
    ax.plot([0.900, 0.900], [0.500, 0.225], color=GREEN, lw=1.7, zorder=4)
    arrow(ax, (0.900, 0.225), (0.784, 0.225), color=GREEN)
    ax.text(0.912, 0.365, "Q, WHP, FLP, BHP\nmeasured every 1 h", ha="left", va="center",
            fontsize=8.2, color=GREEN, fontweight="bold", linespacing=1.4)
    arrow(ax, (0.540, 0.225), (0.500, 0.225), color=RED)
    arrow(ax, (0.368, 0.375), (0.368, 0.500), color="#6A1B9A")
    ax.text(0.360, 0.437, "gain curves + τ", ha="right", va="center", fontsize=8.2,
            color="#6A1B9A", fontweight="bold")
    arrow(ax, (0.430, 0.375), (0.600, 0.500), color="#6A1B9A", rad=0.12)

    ax.text(0.5, 0.022,
            "Layer 1 answers “where should the well end up?”   •   "
            "Layer 2 answers “how do we get there without ever leaving the envelope?”",
            ha="center", va="bottom", fontsize=9.3, style="italic", color="#444")

    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "control_architecture.png"), dpi=190, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
