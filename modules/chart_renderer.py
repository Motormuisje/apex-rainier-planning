"""
Chart renderer — generates matplotlib PNG images for Excel export.

Each method takes Python data directly (no cell references) and returns
a BytesIO containing a PNG. openpyxl's Image class embeds the bytes.

Color palette mirrors the web UI and VBA reference workbook.
"""

from __future__ import annotations

import io
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use('Agg')  # no display needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np


# ---------------------------------------------------------------------------
# Shared style helpers
# ---------------------------------------------------------------------------

_COLORS = {
    'turnover':         '#2E75B6',
    'cogs':             '#C00000',
    'gross_margin':     '#70AD47',
    'inventory_value':  '#ED7D31',
    'ebit':             '#2E75B6',
    'capital':          '#ED7D31',
    'cashflow':         '#70AD47',
    'roce':             '#2E75B6',
    'target':           '#FF0000',
    'average':          '#A0D078',
    # Inventory quality bands (match VBA)
    'under':            '#C00000',
    'safety':           '#196B24',
    'strategic':        '#BE8C00',
    'normal':           '#FFC000',
    'overstock':        '#FF0000',
    'actual_stock':     '#800080',
    'cog_line':         '#ADD8E6',
    # Scatter quadrant colours (match VBA hex)
    'scatter_green':    '#C6EFCE',
    'scatter_red':      '#FFC7CE',
    'scatter_orange':   '#FFC896',
}

_BAND_ORDER = ['under', 'safety', 'strategic', 'normal', 'overstock']
_BAND_LABELS = {
    'under':     'Under',
    'safety':    'Safety Stock',
    'strategic': 'Strategic Stock',
    'normal':    'Normal Variation',
    'overstock': 'Overstock',
}


def _euro_formatter(x, _):
    """Format axis ticks as €1,234k or €1.2M."""
    if abs(x) >= 1_000_000:
        return f'€{x/1_000_000:.1f}M'
    if abs(x) >= 1_000:
        return f'€{x/1_000:.0f}k'
    return f'€{x:.0f}'


def _pct_formatter(x, _):
    return f'{x*100:.1f}%'


def _fig_to_bytes(fig: plt.Figure) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf


def _short_labels(periods: List[str]) -> List[str]:
    """'2025-01' → 'Jan-25' for readable axis ticks."""
    month_abbr = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    labels = []
    for p in periods:
        try:
            y, m = p.split('-')
            labels.append(f"{month_abbr[int(m)-1]}-{y[2:]}")
        except Exception:
            labels.append(p)
    return labels


# ---------------------------------------------------------------------------
# 1. Financial Metrics line chart
# ---------------------------------------------------------------------------

def financial_metrics(
    periods: List[str],
    series: Dict[str, List[float]],
    width_cm: float = 20,
    height_cm: float = 12,
) -> io.BytesIO:
    """Line chart: TURNOVER / COST OF GOODS / GROSS MARGIN / INVENTORY VALUE."""
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))
    x = np.arange(len(periods))
    labels = _short_labels(periods)

    color_map = {
        'TURNOVER':        _COLORS['turnover'],
        'COST OF GOODS':   _COLORS['cogs'],
        'GROSS MARGIN':    _COLORS['gross_margin'],
        'INVENTORY VALUE': _COLORS['inventory_value'],
    }
    for name, values in series.items():
        ax.plot(x, values, marker='o', markersize=4,
                label=name, color=color_map.get(name))

    ax.set_title('Projected Financial Metrics', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.legend(fontsize=8)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 2. ROCE Components line chart
# ---------------------------------------------------------------------------

def roce_components(
    periods: List[str],
    series: Dict[str, List[float]],
    width_cm: float = 20,
    height_cm: float = 12,
) -> io.BytesIO:
    """Line chart: EBIT / CAPITAL INVESTMENT / OPERATIONAL CASHFLOW."""
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))
    x = np.arange(len(periods))
    labels = _short_labels(periods)

    color_map = {
        'EBIT':                 _COLORS['ebit'],
        'CAPITAL INVESTMENT':   _COLORS['capital'],
        'OPERATIONAL CASHFLOW': _COLORS['cashflow'],
    }
    for name, values in series.items():
        ax.plot(x, values, marker='o', markersize=4,
                label=name, color=color_map.get(name))

    ax.set_title('ROCE Components', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.legend(fontsize=8)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 3. ROCE bar + 15% target line + average line
# ---------------------------------------------------------------------------

def roce_bar(
    periods: List[str],
    roce_values: List[float],
    target: float = 0.15,
    average: Optional[float] = None,
    width_cm: float = 20,
    height_cm: float = 12,
) -> io.BytesIO:
    """Bar chart (ROCE %) with dashed 15% target and green average line."""
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))
    x = np.arange(len(periods))
    labels = _short_labels(periods)

    bars = ax.bar(x, roce_values, color=_COLORS['roce'], label='ROCE')
    # colour bars red when below target
    for bar, val in zip(bars, roce_values):
        if val < target:
            bar.set_color('#C00000')

    ax.axhline(target, color=_COLORS['target'], linestyle='--', linewidth=1.5,
               label=f'Target {target*100:.0f}%')
    if average is not None:
        ax.axhline(average, color=_COLORS['average'], linestyle='-', linewidth=2,
                   label=f'Average {average*100:.1f}%')

    ax.set_title('ROCE', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_pct_formatter))
    ax.legend(fontsize=8)
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 4. Top 10 Overstocks stacked bar
# ---------------------------------------------------------------------------

def top10_overstocks(
    periods: List[str],
    materials: List[Dict],
    width_cm: float = 25,
    height_cm: float = 15,
) -> io.BytesIO:
    """Stacked column chart — one series per material.

    materials: list of {'name': str, 'values': List[float]}  (one per period)
    """
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))
    x = np.arange(len(periods))
    labels = _short_labels(periods)
    cmap = plt.get_cmap('tab10')

    bottom = np.zeros(len(periods))
    for i, mat in enumerate(materials):
        vals = np.array(mat['values'], dtype=float)
        ax.bar(x, vals, bottom=bottom, label=mat['name'],
               color=cmap(i % 10), width=0.6)
        bottom += vals

    ax.set_title('Top 10 Overstocks', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.legend(fontsize=7, loc='upper left', bbox_to_anchor=(1, 1))
    ax.grid(axis='y', linestyle='--', alpha=0.4)
    fig.tight_layout()
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 5. Inventory Quality stacked bar + Actual Stock + COGS lines
# ---------------------------------------------------------------------------

def inventory_quality(
    periods: List[str],
    band_data: Dict[str, List[float]],
    actual_stock: List[float],
    cogs: List[float],
    width_cm: float = 30,
    height_cm: float = 18,
) -> io.BytesIO:
    """Stacked column chart (5 bands) with Actual Stock and COGS line overlays.

    band_data keys: 'under', 'safety', 'strategic', 'normal', 'overstock'
    """
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))
    x = np.arange(len(periods))
    labels = _short_labels(periods)

    bottom = np.zeros(len(periods))
    for band in _BAND_ORDER:
        vals = np.array(band_data.get(band, [0] * len(periods)), dtype=float)
        ax.bar(x, vals, bottom=bottom,
               label=_BAND_LABELS[band],
               color=_COLORS[band], width=0.6)
        bottom += vals

    ax2 = ax.twinx()
    ax2.plot(x, actual_stock, color=_COLORS['actual_stock'], linewidth=1.5,
             marker='o', markersize=3, label='Actual Stock')
    ax2.plot(x, cogs, color=_COLORS['cog_line'], linewidth=2.5,
             marker='s', markersize=3, label='Cost of Goods')
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax2.set_ylabel('€ Value', fontsize=8)

    ax.set_title('Inventory Quality', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.set_ylabel('€ Value (bands)', fontsize=8)

    # Combined legend
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc='upper left',
              bbox_to_anchor=(0, 1), ncol=2)
    ax.grid(axis='y', linestyle='--', alpha=0.3)
    fig.tight_layout()
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# 6. MoM Scatter
# ---------------------------------------------------------------------------

def mom_scatter(
    materials: List[str],
    previous: List[float],
    current: List[float],
    hex_colors: List[str],
    width_cm: float = 25,
    height_cm: float = 15,
) -> io.BytesIO:
    """Scatter chart: Previous vs Current inventory, quadrant-coloured dots."""
    fig, ax = plt.subplots(figsize=(width_cm / 2.54, height_cm / 2.54))

    color_map = {
        'C6EFCE': '#C6EFCE',  # green
        'FFC7CE': '#FFC7CE',  # red
        'FFC896': '#FFC896',  # orange
    }
    edge_map = {
        'C6EFCE': '#196B24',
        'FFC7CE': '#C00000',
        'FFC896': '#E07000',
    }

    for px, cy, hx, mat in zip(previous, current, hex_colors, materials):
        fc = color_map.get(hx, '#CCCCCC')
        ec = edge_map.get(hx, '#666666')
        ax.scatter(px, cy, c=fc, edgecolors=ec, s=60, linewidths=0.8, zorder=3)

    # Diagonal reference line (no change)
    all_vals = list(previous) + list(current)
    if all_vals:
        mn, mx = min(all_vals), max(all_vals)
        pad = (mx - mn) * 0.05 if mx != mn else 1
        ax.plot([mn - pad, mx + pad], [mn - pad, mx + pad],
                color='#888888', linestyle='--', linewidth=1, zorder=1)

    ax.set_xlabel('Previous Cycle Inventory', fontsize=9)
    ax.set_ylabel('Current Cycle Inventory', fontsize=9)
    ax.set_title('MoM Inventory Scatter', fontsize=11, fontweight='bold')
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_euro_formatter))
    ax.grid(linestyle='--', alpha=0.3)

    # Legend patches
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='#C6EFCE', edgecolor='#196B24', label='Improved (Q4)'),
        Patch(facecolor='#FFC7CE', edgecolor='#C00000', label='Recovered from negative (Q1)'),
        Patch(facecolor='#FFC896', edgecolor='#E07000', label='Negative (Q2/Q3)'),
    ]
    ax.legend(handles=legend_elements, fontsize=8)
    fig.tight_layout()
    return _fig_to_bytes(fig)
