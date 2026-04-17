// ===== sop_planning.js =====
// S&OP Planning â€” Tooltip & Dependency Highlighting Module
// All tooltip and cell-dependency features for Planning + Values Planning tables.
// Depends on globals resolved at call-time: state, fmt, fmtVal

// ===== LINE TYPE INFO =====
const LT_INFO = {
  '01. Demand forecast': {
    title: '01. Demand forecast',
    desc: 'Directe marktvraag naar dit materiaal, rechtstreeks geladen uit de Forecast sheet.',
    formula: 'LT01[t] = forecast_sheet[materiaal][t]',
    aux: 'Aux 1: Gemiddelde actuals (historische periodes)\nAux 2: Gemiddelde forecast (toekomstige periodes)',
    deps: [],
  },
  '02. Dependent demand': {
    title: '02. Dependent demand',
    desc: 'Afgeleide vraag die ontstaat doordat dit materiaal als component (grondstof of halfproduct) benodigd is voor de productie van een of meer bovenliggende (parent) producten.',
    formula: 'LT02[t] = som(production plan[parent][t] x BOM-qty per parent)\n(sommatie over alle parents die dit materiaal als component hebben)',
    aux: 'Aux 1: Gemiddelde afhankelijke vraag per periode (som van alle parents)',
    deps: ['06. Production plan'],
  },
  '03. Total demand': {
    title: '03. Total demand',
    desc: 'Totale vraag = directe marktvraag (LT01) + alle afhankelijke vraag vanuit parent-producten (LT02). LT02 is al de som van alle parents.',
    formula: 'LT03[t] = LT01[t] + LT02[t]',
    aux: '',
    deps: ['01. Demand forecast', '02. Dependent demand'],
  },
  '04. Inventory': {
    title: '04. Inventory',
    desc: 'Verwachte voorraad aan het einde van elke periode. Begint met de opening stock en wordt elke maand bijgewerkt: aanvoer erbij, vraag eraf. Kan negatief worden (backorder).',
    formula: 'inventory(m) = voorraad vorige maand - total demand(m) + production plan(m) + purchase receipt(m)\ninventory(0) = opening stock (uit Stock level sheet)',
    aux: 'Beginstand: opening stock uit de Stock level sheet',
    deps: ['03. Total demand', '06. Production plan', '06. Purchase receipt'],
    depsHighlight: [
      { lt: '04. Inventory',       periodOffset: -1 },
      { lt: '03. Total demand',    periodOffset:  0 },
      { lt: '06. Production plan', periodOffset:  0 },
      { lt: '06. Purchase receipt',periodOffset:  0 },
    ],
  },
  '05. Minimum target stock': {
    title: '05. Minimum target stock',
    desc: 'Minimale doelvoorraad die gehandhaafd moet worden. Bestaat uit safety stock + strategic stock.',
    formula: 'LT05 = safety_stock + strategic_stock (constant)\n  of moving average methode (3-maands venster)',
    aux: 'Aux 1: Target stock waarde (uit Safety stock sheet)\nAux 2: Coverage in maanden = target stock / gemiddelde maandelijkse LT03 Total demand. [!] betekent dat de target stock meer dan 6 maanden vraag afdekt; check dan of safety/strategic stock bewust zo hoog is.',
    deps: [],
  },
  '06. Production plan': {
    title: '06. Production plan',
    desc: 'Geplande productiehoeveelheid om de doelvoorraad te halen. De benodigde hoeveelheid wordt afgerond naar boven op het dichtstbijzijnde veelvoud van de BOM-header hoeveelheid.',
    formula: 'behoefte[t] = LT05[t] - LT04[t-1] + LT03[t]\nLT06_prod[t] = afgerond naar boven op veelvoud van BOM-header-qty\n               (x productiefractie bij purchased & produced)',
    aux: 'Aux 1: BOM header quantity - het afrondingsveelvoud voor de productieplanning\nAux 2: Productiefractie (alleen bij purchased & produced)',
    deps: ['03. Total demand', '05. Minimum target stock'],
    sourceAux: ['aux1', 'aux2'],
    depsHighlight: [
      { lt: '03. Total demand',        periodOffset:  0 },
      { lt: '05. Minimum target stock', periodOffset: 0 },
      { lt: '04. Inventory',           periodOffset: -1 },
    ],
  },
  '06. Purchase receipt': {
    title: '06. Purchase receipt',
    desc: 'Geplande inkoopontvangst. De benodigde hoeveelheid wordt afgerond naar boven op de MOQ. Periodes binnen de lead time (frozen zone) worden vervangen door werkelijke PO-aantallen.',
    formula: 'behoefte[t] = LT05[t] - LT04[t-1] + LT03[t]\nLT06_purch[t] = afgerond naar boven op de MOQ\nTussenstap: niet-afgeronde behoefte = LT05[t] - LT04[t-1] + LT03[t]\nFrozen periodes: vervangen door werkelijke inkooporders uit Purchase sheet',
    aux: 'Aux 1: Minimum order quantity (MOQ) uit Purchase sheet',
    deps: ['03. Total demand', '05. Minimum target stock'],
    sourceAux: ['aux1'],
    depsHighlight: [
      { lt: '03. Total demand',        periodOffset:  0 },
      { lt: '05. Minimum target stock', periodOffset: 0 },
      { lt: '04. Inventory',           periodOffset: -1 },
    ],
  },
  '07. Purchase plan': {
    title: '07. Purchase plan',
    desc: 'Tijdstip waarop de inkooporder geplaatst moet worden, rekening houdend met de lead time.',
    formula: 'LT07_purch[t] = LT06_purch[t + lead_time]',
    aux: 'Aux: Lead time in maanden (uit Purchase sheet)',
    deps: ['06. Purchase receipt'],
    sourceAux: ['aux1'],
    depsHighlight: [
      { lt: '06. Purchase receipt', useLeadTime: true },
    ],
  },
  '07. Capacity utilization': {
    title: '07. Capacity utilization',
    desc: 'Benodigde machine-uren op basis van de productieplanning en de routing. Een rij per (materiaal x work center).',
    formula: 'AUX2 = base_qty / std_time   (doorvoersnelheid)\nLT07_cap[t] = production_plan[t] / AUX2',
    aux: 'Aux 1: Machinegroepen = work center / machine-groep | Truckgroepen = tijd per trip\nAux 2: Machinegroepen = doorvoersnelheid (base_qty / std_time) | Truckgroepen = ton per trip',
    deps: ['06. Production plan'],
    sourceAux: ['aux2'],
  },
  '08. Dependent requirements': {
    title: '08. Dependent requirements',
    desc: 'Benodigde hoeveelheid van deze grondstof of dit halfproduct, berekend vanuit de productieplanning van het bovenliggende (parent) product.',
    formula: 'LT08[t] = productieplanning[parent][t] x BOM-qty',
    aux: 'Aux 1: Nummer/naam van het bovenliggende product waarvoor deze grondstof gebruikt wordt\nAux 2: BOM-qty - hoeveel van deze grondstof nodig is per eenheid afgewerkt product',
    deps: ['06. Production plan'],
    sourceAux: ['aux2'],
  },
  '09. Available capacity': {
    title: '09. Available capacity',
    desc: 'Beschikbare machine-uren na verrekening van OEE en periodieke beschikbaarheid.',
    formula: 'LT09[t] = shift_hours x OEE x availability[t]',
    aux: 'Aux 1: Shift uren per maand (2-shift ~347, 3-shift =520, 24/7 =730)\nAux 2: Gem. beschikbaarheid over de horizon [!] = <100%',
    deps: [],
  },
  '10. Utilization rate': {
    title: '10. Utilization rate',
    desc: 'Bezettingsgraad van de machine: hoeveel procent van de beschikbare shift-uren daadwerkelijk benut wordt. Let op: OEE zit verwerkt in de teller (LT07), maar NIET in de noemer.',
    formula: 'LT10[t] = LT07_cap[t] / (shift_uren x beschikbaarheid[t])\n         = LT07_cap[t] / (LT09[t] / OEE)\nOEE zit WEL in LT07 (teller) maar NIET in de noemer',
    aux: 'Aux 1: Gemiddelde bezettingsgraad over de hele planningshorizon',
    deps: ['07. Capacity utilization', '09. Available capacity'],
  },
  '11. Shift availability': {
    title: '11. Shift availability',
    desc: 'Beschikbare uren per shift-systeem per maand. Vaste waarde bepaald door het shift-rooster van de machine-groep.',
    formula: '2-shift: 4160 uur/jaar / 12 ~ 347 uur/maand\n3-shift: 6240 uur/jaar / 12 = 520 uur/maand\n24/7:    8760 uur/jaar / 12 = 730 uur/maand',
    aux: 'Aux 1: Shift-systeem van deze machine-groep (2-shift / 3-shift / 24-7)\nAux 2: Gemiddelde beschikbaarheidsfactor over de horizon [!] = <100%',
    deps: [],
  },
  '12. FTE requirements': {
    title: '12. FTE requirements',
    desc: 'Benodigde FTE op basis van de benodigde machine-uren (LT07) en de productieve uren per FTE per jaar.',
    formula: 'LT12[t] = LT07_cap[t] / (FTE_uren_per_jaar / 12)\nStandaard FTE_uren_per_jaar = 1492',
    aux: 'Aux 1: Work center / machine-groep waarop deze FTE-berekening betrekking heeft\nAux 2: Productieve FTE-uren per jaar (standaard 1492 uur)',
    deps: ['07. Capacity utilization'],
    sourceAux: ['aux2'],
  },
  '13. Consolidation': {
    title: '13. Consolidation',
    desc: "Financiele P&L samenvatting op site-niveau. Alle volumes worden omgezet naar euro's via prijzen en kosten uit de master data.",
    formula: 'Turnover    = LT01 volume x gemiddelde verkoopprijs\nGrondstof   = LT03 volume x cost_per_unit (inkoop)\nMachine     = LT07 uren x cost_per_machine_uur\nDirect FTE  = LT12 FTE x direct_fte_cost/maand\nGross margin= Turnover - Grondstof - Machine - FTE\nEBITDA      = Gross margin - Overhead - SGA\nEBIT        = EBITDA - Afschrijving/12\nROCE        = EBIT x 12 / (Net book value + Werkkapitaal)',
    aux: 'Aux 1: Driver of bronlijn voor deze consolidatieregel\nAux 2: Samenvattingswaarde volgens de specifieke consolidatieregel\nWerkkapitaal: (Debiteuren via DSO) - (Crediteuren via DPO) + Voorraadwaarde',
    deps: [],
  },
};

const VALUE_AUX_INFO = {
  '01. Demand forecast': {
    aux1: 'Gemiddelde verkoopprijs per eenheid. Als je deze wijzigt, verandert de omzet van dit materiaal in alle periodes mee.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '03. Total demand': {
    aux1: 'Kostprijs van de grondstof per eenheid. Als je deze wijzigt, verandert de grondstofkost van dit materiaal in alle periodes mee.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '04. Inventory': {
    aux1: 'Voorraadwaarde per eenheid. Als je deze wijzigt, veranderen zowel de voorraadwaarde per periode als de startwaarde van deze rij.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '06. Purchase receipt': {
    aux1: 'Waardering per eenheid van de inkoopontvangst. Waarde per periode = LT06 Purchase receipt volume x deze kostprijs.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '07. Capacity utilization': {
    aux1: 'Machinekost per uur. Als je deze wijzigt, verandert de machinekost van deze rij in alle periodes mee.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '12. FTE requirements': {
    aux1: 'Directe FTE-kost per maand. Als je deze wijzigt, verandert de personeelskost van deze rij in alle periodes mee.',
    aux2: 'Niet gebruikt op deze individuele value row.',
  },
  '13. Consolidation': {
    aux1: 'Aux 1: Referentie naar de gebruikte bronlijn, valuation parameter of samengestelde P&L-regel.',
    aux2: 'Aux 2: Samenvattingswaarde volgens de specifieke consolidatieregel.',
  },
};

const VALUE_ROW_INFO = {
  '01. Demand forecast': {
    noun: 'Omzet',
    desc: 'Verkoopwaarde van dit materiaal in deze periode: forecastvolume maal gemiddelde verkoopprijs.',
    valueLabel: 'Omzet',
    driverLabel: 'Gemiddelde verkoopprijs',
    calcLt: '01. Demand forecast',
    calcLabel: 'forecastvolume',
  },
  '03. Total demand': {
    noun: 'Raw material cost',
    desc: 'Grondstofkost van dit materiaal in deze periode: totale vraag maal kostprijs per eenheid.',
    valueLabel: 'Grondstofkost',
    driverLabel: 'Kostprijs per eenheid',
    calcLt: '03. Total demand',
    calcLabel: 'totale vraag',
  },
  '04. Inventory': {
    noun: 'Inventory value',
    desc: 'Voorraadwaarde van dit materiaal aan het einde van deze periode: voorraadpositie maal voorraadwaardering per eenheid.',
    startDesc: 'Voorraadwaarde van de beginvoorraad voor deze rij: starting stock maal voorraadwaardering per eenheid.',
    valueLabel: 'Voorraadwaarde',
    driverLabel: 'Voorraadwaardering per eenheid',
    calcLt: '04. Inventory',
    calcLabel: 'voorraadpositie',
  },
  '06. Purchase receipt': {
    noun: 'Purchase receipt value',
    desc: 'Waarde van de geplande inkoopontvangst in deze periode: ontvangstvolume maal inkoopkost per eenheid.',
    valueLabel: 'Inkoopontvangstwaarde',
    driverLabel: 'Inkoopkost per eenheid',
    calcLt: '06. Purchase receipt',
    calcLabel: 'purchase receipt volume',
  },
  '07. Capacity utilization': {
    noun: 'Machine cost',
    desc: 'Machinekost van dit materiaal en deze work center in deze periode: benodigde machine-uren maal machinekost per uur.',
    valueLabel: 'Machinekost',
    driverLabel: 'Machinekost per uur',
    calcLt: '07. Capacity utilization',
    calcLabel: 'machine-uren',
  },
  '12. FTE requirements': {
    noun: 'Direct FTE cost',
    desc: 'Directe personeelskost in deze periode: benodigde FTE maal directe FTE-kost per maand.',
    valueLabel: 'Directe FTE-kost',
    driverLabel: 'Directe FTE-kost per maand',
    calcLt: '12. FTE requirements',
    calcLabel: 'benodigde FTE',
  },
};

const FINANCE_KPI_INFO = {
  turnover: {
    title: 'Turnover',
    text: 'Omzet uit verkoop. In deze tool is dat forecastvolume maal gemiddelde verkoopprijs.',
    drivers: 'Beinvloed door: LT01 Demand forecast en Value Planning Aux 1 van 01. Demand forecast.',
  },
  raw_material_cost: {
    title: 'Raw Material Cost',
    text: 'Kost van ingekochte grondstoffen of componenten die nodig zijn om aan de vraag te voldoen.',
    drivers: 'Beinvloed door: LT03 Total demand, LT06 Purchase receipt en de grondstofkost in Value Planning Aux 1.',
  },
  machine_cost: {
    title: 'Machine Cost',
    text: 'Machinekost op basis van gebruikte machine-uren maal machinekost per uur.',
    drivers: 'Beinvloed door: LT07 Capacity utilization en de machinekost per uur in Value Planning Aux 1.',
  },
  direct_fte_cost: {
    title: 'Direct FTE Cost',
    text: 'Loonkost van direct productiepersoneel dat rechtstreeks aan de operatie werkt.',
    drivers: 'Beinvloed door: LT12 FTE requirements en Direct FTE cost per maand (valuation parameter 1).',
  },
  indirect_fte_cost: {
    title: 'Indirect FTE Cost',
    text: 'Loonkost van indirect personeel, zoals planning, logistiek, techniek of ondersteuning.',
    drivers: 'Beinvloed door: Indirect FTE cost per maand (valuation parameter 2).',
  },
  overhead_cost: {
    title: 'Overhead Cost',
    text: 'Vaste algemene kosten van de site, zoals gebouwen, utilities en algemene ondersteuning.',
    drivers: 'Beinvloed door: Overhead cost per maand (valuation parameter 3).',
  },
  cost_of_goods: {
    title: 'Cost of Goods',
    text: 'Totale operationele kost om te produceren en te leveren.',
    drivers: 'Beinvloed door: Raw Material Cost, Machine Cost, Direct FTE Cost, Indirect FTE Cost en Overhead Cost.',
  },
  gross_margin: {
    title: 'Gross Margin',
    text: 'Wat overblijft van de omzet nadat de cost of goods is afgetrokken.',
    drivers: 'Beinvloed door: Turnover en Cost of Goods.',
  },
  sga_cost: {
    title: 'SG&A Cost',
    text: 'Selling, General and Administrative kosten: verkoop, administratie en algemene ondersteuning.',
    drivers: 'Beinvloed door: SG&A cost per maand (valuation parameter 4).',
  },
  ebitda: {
    title: 'EBITDA',
    text: 'Bedrijfsresultaat voor afschrijvingen. Simpel gezegd: wat overblijft na omzet min operationele kosten, maar voor D&A.',
    drivers: 'Beinvloed door: Gross Margin en SG&A Cost.',
  },
  da_cost: {
    title: 'D&A Cost',
    text: 'Depreciation and Amortization: afschrijvingskost van vaste activa, uitgesmeerd over de tijd.',
    drivers: 'Beinvloed door: Depreciation per jaar (valuation parameter 5).',
  },
  ebit: {
    title: 'EBIT',
    text: 'Bedrijfsresultaat na afschrijvingen. Formule: EBITDA min D&A cost.',
    drivers: 'Beinvloed door: EBITDA en D&A Cost.',
  },
  fixed_assets_nbv: {
    title: 'Fixed Assets NBV',
    text: 'Net book value van vaste activa: de resterende boekwaarde van machines, gebouwen en andere investeringen.',
    drivers: 'Beinvloed door: Net book value (valuation parameter 6).',
  },
  inventory_value: {
    title: 'Inventory Value',
    text: 'Waarde van de voorraad op basis van stock maal waardering per eenheid.',
    drivers: 'Beinvloed door: LT04 Inventory, starting stock en de voorraadwaardering in Value Planning Aux 1 van 04. Inventory.',
  },
  receivables: {
    title: 'Receivables',
    text: 'Debiteuren: geld dat klanten nog moeten betalen.',
    drivers: 'Beinvloed door: Turnover en DSO / Days Sales Outstanding (valuation parameter 7).',
  },
  payables: {
    title: 'Payables',
    text: 'Crediteuren: geld dat nog aan leveranciers betaald moet worden.',
    drivers: 'Beinvloed door: Purchase receipt value en DPO / Days Payable Outstanding (valuation parameter 8).',
  },
  working_capital: {
    title: 'Working Capital Requirements',
    text: 'Werkkapitaalbehoefte: receivables plus inventory value min payables.',
    drivers: 'Beinvloed door: Receivables, Inventory Value en Payables.',
  },
  capital_investment: {
    title: 'Capital Investment',
    text: 'Kapitaal dat in de site vastzit: fixed assets net book value plus working capital.',
    drivers: 'Beinvloed door: Fixed Assets NBV en Working Capital Requirements.',
  },
  operational_cashflow: {
    title: 'Operational Cashflow',
    text: 'Vereenvoudigde operationele kasstroom op basis van resultaat en kapitaalbeslag in de tool.',
    drivers: 'Beinvloed door: EBITDA, Inventory Value en working-capital effecten.',
  },
  roce: {
    title: 'ROCE',
    text: 'Return on Capital Employed. Laat zien hoeveel EBIT je haalt op het kapitaal dat in de site vastzit.',
    drivers: 'Beinvloed door: EBIT, Net book value, Inventory Value, DSO en DPO.',
  },
  avg_monthly_turnover: {
    title: 'Avg Monthly Turnover',
    text: 'Gemiddelde maandelijkse omzet. Dit is de verkoopwaarde van de forecast: volume maal gemiddelde verkoopprijs.',
    drivers: 'Beinvloed door: forecastvolume (LT01) en de gemiddelde verkoopprijs in Value Planning Aux 1.',
  },
  avg_monthly_ebitda: {
    title: 'Avg Monthly EBITDA',
    text: 'Gemiddeld maandelijks bedrijfsresultaat voor afschrijvingen. Simpel gezegd: wat overblijft na omzet min operationele kosten, maar voor D&A.',
    drivers: 'Beinvloed door: Turnover, Cost of Goods en SG&A cost per maand (valuation parameter 4).',
  },
  avg_monthly_ebit: {
    title: 'Avg Monthly EBIT',
    text: 'Gemiddeld maandelijks bedrijfsresultaat na afschrijvingen. Formule: EBITDA min D&A cost.',
    drivers: 'Beinvloed door: EBITDA en Depreciation per jaar (valuation parameter 5, omgerekend naar maand).',
  },
  avg_roce: {
    title: 'Avg ROCE',
    text: 'Return on Capital Employed. Laat zien hoeveel operationeel rendement je haalt op het kapitaal dat in de site vastzit.',
    drivers: 'Beinvloed door: EBIT, Net book value (parameter 6), Inventory Value, DSO (parameter 7) en DPO (parameter 8).',
  },
};

// ===== DEPENDENCY COLORS =====
// Index 0 = source (clicked cell's lt), 1..5 = upstream dependencies
const DEP_COLORS = [
    { bg: 'rgba(99,102,241,0.20)',  border: '#6366f1',  text: '#a5b4fc' }, // indigo  â€” source
    { bg: 'rgba(34,197,94,0.18)',   border: '#22c55e',  text: '#86efac' }, // green   â€” dep 1
    { bg: 'rgba(251,191,36,0.18)',  border: '#f59e0b',  text: '#fde68a' }, // amber   â€” dep 2
    { bg: 'rgba(239,68,68,0.18)',   border: '#ef4444',  text: '#fca5a5' }, // red     â€” dep 3
    { bg: 'rgba(14,165,233,0.18)',  border: '#0ea5e9',  text: '#7dd3fc' }, // sky     â€” dep 4
    { bg: 'rgba(168,85,247,0.18)', border: '#a855f7',   text: '#d8b4fe' }, // purple  â€” dep 5
];

// ===== MODULE STATE =====
let tooltipsMode = false;
let editMode     = false;
let cellMarkMode = false;
let activeDepHighlight = null;
let _vpDepSetup = false;
let vpConsolBreakdownActive = false;

function _markTooltipSourceCell(cell) {
    document.querySelectorAll('.tooltip-source-cell').forEach(el => {
        el.classList.remove('tooltip-source-cell');
        if (el.dataset.tooltipSourceCell === '1') {
            el.style.removeProperty('outline');
            el.style.removeProperty('outline-offset');
            el.style.removeProperty('box-shadow');
            delete el.dataset.tooltipSourceCell;
        }
    });
    if (!cell) return;
    cell.classList.add('tooltip-source-cell');
    cell.dataset.tooltipSourceCell = '1';
    cell.style.setProperty('outline', '2px solid #fbbf24', 'important');
    cell.style.setProperty('outline-offset', '-2px');
    cell.style.setProperty('box-shadow', 'inset 0 0 0 1px rgba(15,23,42,0.85), 0 0 0 1px rgba(251,191,36,0.35)');
}

// ===== EDIT MODE TOGGLE =====
function toggleEditMode() {
    editMode = !editMode;
    // Mutually exclusive: turning on edit mode disables tooltips mode
    if (editMode && tooltipsMode) {
        tooltipsMode = false;
        document.querySelectorAll('.tooltips-btn').forEach(btn => {
            btn.classList.remove('active');
            btn.innerHTML = '&#128712; Tooltips';
        });
        document.body.classList.remove('tooltips-mode');
        const t = document.getElementById('ltInfoTooltip');
        if (t) t.style.display = 'none';
        _clearDepHighlight();
    }
    document.querySelectorAll('.edit-mode-btn').forEach(btn => {
        btn.classList.toggle('active', editMode);
        btn.innerHTML = editMode ? '&#9998; Bewerken aan' : '&#9998; Bewerken';
    });
    document.body.classList.toggle('edit-mode', editMode);
    // When turning OFF edit mode: restore original text and blur any active editable cell
    if (!editMode) {
        const active = document.querySelector('#planBody td.editable-cell[contenteditable="true"], #vpBody td.editable-cell[contenteditable="true"]');
        if (active) {
            if (active.dataset.displayOriginal !== undefined) active.textContent = active.dataset.displayOriginal;
            else {
                const ov = active.hasAttribute('data-original') ? parseFloat(active.dataset.original) : NaN;
                if (!isNaN(ov)) active.textContent = fmt(ov);
            }
            active.blur(); // focusout handler cleans up contenteditable + data-original
        }
    }
    // Cells become editable on-demand when clicked (see click handler in _setupPlanTableDelegation)
}

// ===== TOOLTIPS MODE TOGGLE =====
function toggleTooltipsMode() {
    tooltipsMode = !tooltipsMode;
    // Mutually exclusive: turning on tooltips mode disables edit mode
    if (tooltipsMode && editMode) {
        editMode = false;
        document.querySelectorAll('.edit-mode-btn').forEach(btn => {
            btn.classList.remove('active');
            btn.innerHTML = '&#9998; Bewerken';
        });
        document.body.classList.remove('edit-mode');
        // Restore original text and blur any active editable cell
        const active = document.querySelector('#planBody td.editable-cell[contenteditable="true"], #vpBody td.editable-cell[contenteditable="true"]');
        if (active) {
            if (active.dataset.displayOriginal !== undefined) active.textContent = active.dataset.displayOriginal;
            else {
                const ov = active.hasAttribute('data-original') ? parseFloat(active.dataset.original) : NaN;
                if (!isNaN(ov)) active.textContent = fmt(ov);
            }
            active.blur(); // focusout handler cleans up contenteditable + data-original
        }
    }
    document.querySelectorAll('.tooltips-btn').forEach(btn => {
        btn.classList.toggle('active', tooltipsMode);
        btn.innerHTML = tooltipsMode ? '&#128712; Tooltips aan' : '&#128712; Tooltips';
    });
    document.body.classList.toggle('tooltips-mode', tooltipsMode);
    if (!tooltipsMode) {
        const t = document.getElementById('ltInfoTooltip');
        if (t) t.style.display = 'none';
        _clearDepHighlight();
    }
}

function toggleDepHighlightMode() {
    cellMarkMode = !document.body.classList.contains('cell-mark-mode');
    document.body.classList.toggle('cell-mark-mode', cellMarkMode);
    document.querySelectorAll('.dep-highlight-btn').forEach(btn => {
        btn.classList.toggle('active', cellMarkMode);
        btn.innerHTML = cellMarkMode ? '&#9679; Markering aan' : '&#9679; Markering';
    });
    if (cellMarkMode && editMode) {
        editMode = false;
        document.body.classList.remove('edit-mode');
        document.querySelectorAll('.edit-mode-btn').forEach(btn => {
            btn.classList.remove('active');
            btn.innerHTML = '&#9998; Bewerken';
        });
        const active = document.querySelector('#planBody td.editable-cell[contenteditable="true"], #vpBody td.editable-cell[contenteditable="true"]');
        if (active) active.blur();
    }
    if (!cellMarkMode && typeof _clearRangeSelection === 'function') {
        _clearRangeSelection();
        if (typeof _clearActiveTableCell === 'function') _clearActiveTableCell();
        try {
            if (Array.isArray(_storedSelections)) _storedSelections = [];
        } catch (_) {}
    }
}

function _isDepHighlightEnabled() {
    return !!tooltipsMode;
}

function _isCellMarkModeEnabled() {
    return document.body.classList.contains('cell-mark-mode');
}

// ===== TOOLTIP POSITIONING =====
function _posTooltip(tipEl, e, opts = {}) {
    const margin = 12;
    const pad = 8;
    const W = window.innerWidth;
    const H = window.innerHeight;
    const tipW = Math.max(tipEl.offsetWidth || 0, 220);
    const tipH = Math.max(tipEl.offsetHeight || 0, 80);
    const preferredSide = opts.preferredSide || 'right';

    let x = e.clientX + margin;
    let y = e.clientY + margin;

    const anchorCell = e.target && typeof e.target.closest === 'function'
        ? e.target.closest('td[data-tt], [data-fin-tt]')
        : null;

    if (anchorCell) {
        const rect = anchorCell.getBoundingClientRect();
        if (preferredSide === 'left') {
            x = rect.left - tipW - margin;
            if (x < pad) x = rect.right + margin;
        } else {
            x = rect.right + margin;
            if (x + tipW > W - pad) x = rect.left - tipW - margin;
        }
        y = rect.top + (rect.height / 2) - (tipH / 2);

        const overlapsX = x < rect.right && (x + tipW) > rect.left;
        const overlapsY = y < rect.bottom && (y + tipH) > rect.top;
        if (overlapsX && overlapsY) {
            y = rect.bottom + margin;
            if (y + tipH > H - pad) y = rect.top - tipH - margin;
        }
    }

    x = Math.max(pad, Math.min(W - tipW - pad, x));
    y = Math.max(pad, Math.min(H - tipH - pad, y));
    tipEl.style.left = `${Math.round(x)}px`;
    tipEl.style.top = `${Math.round(y)}px`;
}

// ===== FORMULA COLOR RENDERER =====
function _renderFormula(lt) {
    const info = LT_INFO[lt];
    if (!info || !info.formula) return '';

    const depsHL = info.depsHighlight || (info.deps || []).map(d => ({ lt: d, periodOffset: 0 }));

    // Build LT-number â†’ color map: source = indigo (0), deps = colors 1..N
    const ltColorMap = {}; // '04' â†’ DEP_COLORS[n]
    const addLt = (ltName, idx) => {
        const m = String(ltName).match(/^(\d+)\./);
        if (m) ltColorMap[m[1].padStart(2, '0')] = DEP_COLORS[Math.min(idx, DEP_COLORS.length - 1)];
    };
    addLt(lt, 0);
    depsHL.forEach((d, i) => addLt(d.lt, i + 1));

    let text = info.formula.replace(/behoefte/g, 'need');

    // LT04 uses natural-language formula â€” do ordered replacements (longer first)
    if (lt === '04. Inventory') {
        const [c0, c1, c2, c3, c4] = [0,1,2,3,4].map(i => DEP_COLORS[Math.min(i, DEP_COLORS.length-1)]);
        const s = (v, c) => `<span style="color:${c.text};font-weight:600">${v}</span>`;
        text = text
            .replace('inventory(m-1)',  s('inventory(m-1)',  c1))
            .replace('inventory(0)',    s('inventory(0)',    c1))
            .replace('total demand(m)', s('total demand(m)', c2))
            .replace('production plan(m)', s('production plan(m)', c3))
            .replace('purchase receipt(m)', s('purchase receipt(m)', c4))
            .replace('inventory(m)',    s('inventory(m)',    c0));
        return text;
    }

    // For all other LTs: regex-replace LT{nn}[_suffix][subscript] references
    // Process longer LT numbers first to avoid partial matches
    const s = (v, c) => `<span style="color:${c.text};font-weight:600">${v}</span>`;
    Object.entries(ltColorMap)
        .sort(([a], [b]) => b.localeCompare(a))
        .forEach(([num, color]) => {
            const re = new RegExp(`LT${num}(?:_[a-z]+)?(?:\\[[^\\]]*\\])?`, 'g');
            text = text.replace(re, m => s(m, color));
        });

    // Color 'need[t]' as source color
    text = text.replace(/\bneed\[t\]/g, s('need[t]', DEP_COLORS[0]));

    // In LT07 Capacity utilization: color 'production_plan[t]' (= LT06 dep) and 'AUX2' (source aux)
    if (lt === '07. Capacity utilization') {
        const c06 = ltColorMap['06'];
        if (c06) text = text.replace(/production_plan\[t\]/g, s('production_plan[t]', c06));
        text = text.replace(/\bAUX2\b/g, s('AUX2', DEP_COLORS[0]));
    }

    // In LT08 / LT02: color 'productieplanning[parent][t]' as LT06 dep
    if (lt === '08. Dependent requirements' || lt === '02. Dependent demand') {
        const c06 = ltColorMap['06'];
        if (c06) text = text.replace(/productieplanning\[parent\]\[t\]/g, s('productieplanning[parent][t]', c06));
    }

    return text;
}

// ===== LT TOOLTIP HTML BUILDERS =====
function _isNumericLike(v) {
    if (v === undefined || v === null) return false;
    const n = Number(String(v).replace('%', '').trim());
    return Number.isFinite(n);
}

function _normKey(v) {
    return String(v == null ? '' : v).trim();
}

function _pickRowForContext(rows, matNum, rowCtx) {
    const candidates = (rows || []).filter(r => String(r.material_number) === String(matNum));
    if (!candidates.length) return null;
    if (!rowCtx) return candidates[0];

    const ctxAux1 = _normKey(rowCtx.aux1 || rowCtx.aux);
    const ctxAux2 = _normKey(rowCtx.aux2);

    if (ctxAux1) {
        const matchAux1 = candidates.find(r => _normKey(r.aux_column) === ctxAux1);
        if (matchAux1) return matchAux1;
    }
    if (ctxAux2) {
        const matchAux2 = candidates.find(r => _normKey(r.aux_2_column).replace(/!$/, '') === ctxAux2.replace(/!$/, ''));
        if (matchAux2) return matchAux2;
    }
    return candidates[0];
}

function _roundUpToMultiple(value, multiple) {
    const m = Number(multiple);
    if (!Number.isFinite(value) || value <= 0) return 0;
    if (!Number.isFinite(m) || m <= 0) return value;
    return Math.ceil(value / m) * m;
}

function _buildLtTooltipHtml(lt, matNum, rowCtx, period) {
    const info = LT_INFO[lt];
    if (!info) return `<div class="tt-title">${lt}</div>`;
    let html = `<div class="tt-title">${info.title}</div>`;
    html += `<div style="color:#cbd5e1;margin-bottom:4px">${info.desc}</div>`;
    if (info.formula) {
        _renderFormula(lt).split('\n').forEach(line => {
            html += `<span class="tt-formula">${line}</span>`;
        });
    }
    const calcLines = _getLtCalcExample(lt, matNum, period, rowCtx);
    if (calcLines) html += `<div class="tt-calc">${calcLines}</div>`;
    if (info.aux) {
        html += `<div class="tt-aux">${info.aux.replace(/\n/g,'<br>')}</div>`;
    }
    return html;
}

function _getLtCalcExample(lt, matNum, p, rowCtx) {
    if (!state.results || !state.periods || state.periods.length === 0) return null;
    if (!p) p = state.periods[0];

    const fmt1 = v => (v === undefined || v === null) ? '?' : Number(v).toLocaleString(undefined, {maximumFractionDigits:1});
    const getRows = (lineType) => state.results[lineType] || [];
    const pickRow = (lineType, useContext) => {
        const rows = getRows(lineType);
        return useContext ? _pickRowForContext(rows, matNum, rowCtx) : _pickRowForContext(rows, matNum, null);
    };
    const getVal = (lineType, opts = {}) => {
        const row = pickRow(lineType, !!opts.useContext);
        if (!row || !row.values) return null;
        return row.values[p] || 0;
    };

    if (lt === '03. Total demand') {
        const lt01 = getVal('01. Demand forecast');
        const lt02Rows = getRows('02. Dependent demand').filter(r => String(r.material_number) === String(matNum));
        const lt02 = lt02Rows.reduce((s, r) => s + (r.values[p] || 0), 0);
        if (lt01 === null && lt02Rows.length === 0) return null;
        const tot = (lt01 || 0) + lt02;
        return `${p}: ${fmt1(lt01 || 0)} (forecast) + ${fmt1(lt02)} (afhankelijk) = ${fmt1(tot)}`;
    }

    if (lt === '04. Inventory') {
        const lt03 = getVal('03. Total demand');
        const lt06p = getVal('06. Production plan');
        const lt06r = getVal('06. Purchase receipt');
        const lt04 = getVal('04. Inventory');
        if (lt03 === null || lt04 === null) return null;

        const pIdx = state.periods.indexOf(p);
        const prevP = pIdx > 0 ? state.periods[pIdx - 1] : null;
        const lt04row = pickRow('04. Inventory', false);
        if (!lt04row) return null;

        const lt04prev = prevP ? (lt04row.values[prevP] || 0) : (lt04row.starting_stock || 0);
        const prevLabel = prevP ? `voorraad vorige maand (${prevP})` : 'startvoorraad';
        return `${p}: ${fmt1(lt04prev)} (${prevLabel}) - ${fmt1(lt03)} (vraag) + ${fmt1(lt06p || 0)} (productie) + ${fmt1(lt06r || 0)} (inkoop) = ${fmt1(lt04)}`;
    }

    if (lt === '06. Purchase receipt') {
        const lt03 = getVal('03. Total demand');
        const lt05 = getVal('05. Minimum target stock');
        const lt06 = getVal('06. Purchase receipt', { useContext: true });
        if (lt03 === null || lt05 === null || lt06 === null) return null;

        const pIdx = state.periods.indexOf(p);
        const prevP = pIdx > 0 ? state.periods[pIdx - 1] : null;
        const invRow = pickRow('04. Inventory', false);
        const prevStock = invRow ? (prevP ? (invRow.values[prevP] || 0) : (invRow.starting_stock || 0)) : 0;

        const receiptRow = pickRow('06. Purchase receipt', true);
        const moq = receiptRow ? Number(receiptRow.aux_column || 0) : 0;
        const rawNeed = (lt05 || 0) - prevStock + (lt03 || 0);
        const roundedNeed = _roundUpToMultiple(rawNeed, moq);

        const ppRow = pickRow('07. Purchase plan', true) || pickRow('07. Purchase plan', false);
        const leadTime = ppRow ? Math.max(0, Math.round(Number(ppRow.aux_column) || 0)) : 0;
        const frozen = pIdx >= 0 && pIdx < leadTime;

        let line = `${p}: ongeronde behoefte = ${fmt1(lt05 || 0)} - ${fmt1(prevStock)} + ${fmt1(lt03 || 0)} = ${fmt1(rawNeed)}`;
        if (moq > 0) line += ` -> afgerond op MOQ ${fmt1(moq)} = ${fmt1(roundedNeed)}`;
        line += ` -> gebruikt in plan: ${fmt1(lt06)}`;
        if (frozen) line += ' (frozen periode: actual PO kan afronding overrulen)';
        return line;
    }

    if (lt === '07. Purchase plan') {
        const ppRow = pickRow('07. Purchase plan', true) || pickRow('07. Purchase plan', false);
        if (!ppRow) return null;

        const leadTime = Math.max(0, Math.round(Number(ppRow.aux_column) || 0));
        const pIdx = state.periods.indexOf(p);
        const srcIdx = pIdx >= 0 ? pIdx + leadTime : -1;
        const srcPeriod = (srcIdx >= 0 && srcIdx < state.periods.length) ? state.periods[srcIdx] : null;
        const receiptRow = pickRow('06. Purchase receipt', true) || pickRow('06. Purchase receipt', false);
        const ppVal = ppRow.values[p] || 0;
        const monthLbl = leadTime === 1 ? 'maand' : 'maanden';
        if (!srcPeriod || !receiptRow) {
            return `${p}: ${fmt1(ppVal)} (lead time ${leadTime} ${monthLbl}; bronperiode valt buiten horizon)`;
        }
        const receiptVal = receiptRow.values[srcPeriod] || 0;
        return `${p}: ${fmt1(ppVal)} = ${fmt1(receiptVal)} uit LT06 Purchase receipt van ${srcPeriod} (lead time ${leadTime} ${monthLbl}, rekening houdend met lead time)`;
    }

    if (lt === '10. Utilization rate') {
        const lt07 = getVal('07. Capacity utilization');
        const lt10 = getVal('10. Utilization rate');
        if (lt07 === null || lt10 === null) return null;
        return `${p}: ${fmt1(lt07)} machine-uren -> bezettingsgraad = ${fmt1(lt10 * 100)}%`;
    }

    if (lt === '12. FTE requirements') {
        const lt07 = getVal('07. Capacity utilization');
        const lt12 = getVal('12. FTE requirements');
        if (lt07 === null || lt12 === null) return null;
        const fteMonth = (1492 / 12).toFixed(1);
        return `${p}: ${fmt1(lt07)} uur / ${fteMonth} (uur/FTE/maand) = ${fmt1(lt12)} FTE`;
    }

    if (lt === '07. Capacity utilization') {
        const capRow = pickRow('07. Capacity utilization', true) || pickRow('07. Capacity utilization', false);
        const lt07 = capRow && capRow.values ? (capRow.values[p] || 0) : null;
        if (lt07 === null) return null;

        const aux1 = Number(capRow.aux_column || 0);
        const aux2 = Number(capRow.aux_2_column || 0);
        const isTruck = Number.isFinite(aux1) && aux1 > 0 && Number.isFinite(aux2) && aux2 > 0;
        if (isTruck) {
            return `${p}: ${fmt1(lt07)} uur (truckgroep: ${fmt1(aux1)} uur/trip en ${fmt1(aux2)} ton/trip)`;
        }

        const lt06 = getVal('06. Production plan');
        if (lt06 === null) return `${p}: ${fmt1(lt07)} machine-uren`;
        return `${p}: productie ${fmt1(lt06)} -> ${fmt1(lt07)} machine-uren`;
    }

    if (lt === '02. Dependent demand') {
        const lt02row = pickRow('02. Dependent demand', true) || pickRow('02. Dependent demand', false);
        if (!lt02row || !lt02row.values) return null;
        const val = lt02row.values[p] || 0;
        const parent = _normKey(lt02row.aux_column);
        if (parent) return `${p}: ${fmt1(val)} (bijdrage van parent ${parent})`;
        return `${p}: ${fmt1(val)} (afhankelijke vraag voor deze rij)`;
    }

    return null;
}

function _buildTooltipForCell(type, lt, mat, period, sheet, rowCtx, cellCtx) {
    const info = LT_INFO[lt] || null;
    const sheetName = sheet || 'planning';
    const valueRows = state.valueResults && state.valueResults[lt] ? state.valueResults[lt] : [];
    const valueRow = sheetName === 'values' ? _pickRowForContext(valueRows, mat, rowCtx) : null;
    if (type === 'lt') {
        return _buildLtTooltipHtml(lt, mat, rowCtx, period);
    }
    if (type === 'aux1' || type === 'aux2') {
        let auxText = '';
        if (sheetName === 'values' && String(mat || '').startsWith('ZZZZZZ_')) {
            auxText = _getVpConsolAuxText(mat, type);
        } else if (sheetName === 'values' && VALUE_AUX_INFO[lt]) {
            auxText = VALUE_AUX_INFO[lt][type] || '';
        } else {
            const idx = type === 'aux1' ? 0 : 1;
            const auxLines = (info && info.aux) ? info.aux.split('\n') : [];
            auxText = auxLines[idx] || '';
            if (lt === '07. Capacity utilization') {
                const row = _pickRowForContext((state.results && state.results[lt]) || [], mat, rowCtx);
                const aux1Raw = row ? row.aux_column : rowCtx && rowCtx.aux1;
                const aux2Raw = row ? row.aux_2_column : rowCtx && rowCtx.aux2;
                const isTruck = _isNumericLike(aux1Raw) && _isNumericLike(aux2Raw);
                if (isTruck) {
                    auxText = type === 'aux1'
                        ? 'Aux 1: Tijd per trip (uur per trip).'
                        : 'Aux 2: Ton per trip (laadcapaciteit per trip).';
                }
            }
        }
        let html = `<div class="tt-title">${lt} - ${type === 'aux1' ? 'Aux 1' : 'Aux 2'}</div>`;
        if (auxText) html += `<div style="color:#cbd5e1;margin-top:3px">${auxText}</div>`;
        else html += `<div style="color:#94a3b8;font-size:10px;margin-top:3px">Geen aanvullende beschrijving voor dit veld.</div>`;

        // If this is a VP aux1 cell with an active edit: show edit info + consolidation impact.
        if (sheetName === 'values' && type === 'aux1' && !String(mat || '').startsWith('ZZZZZZ_')) {
            const auxEditKey = `${lt}||${mat}`;
            const auxEdit = state.valueAuxEdits && state.valueAuxEdits[auxEditKey];
            if (auxEdit) {
                const orig = Number(auxEdit.original || 0);
                const nv   = Number(auxEdit.new !== undefined ? auxEdit.new : orig);
                const delta = nv - orig;
                const sign  = delta >= 0 ? '+' : '';
                const col   = delta >= 0 ? '#4ade80' : '#f87171';
                const label = (VALUE_AUX_INFO[lt] && VALUE_AUX_INFO[lt].aux1) ? VALUE_AUX_INFO[lt].aux1 : 'Driver';
                html += `<div style="margin-top:6px;padding:5px 7px;background:rgba(255,255,255,0.06);border-radius:5px">`;
                html += `<div style="color:#fde68a;font-weight:600;font-size:10px;margin-bottom:3px">&#9998; Gewijzigde driver (${label})</div>`;
                html += `<div style="font-size:11px">Origineel: <b>${fmtVal(orig)}</b> &rarr; Huidig: <b>${fmtVal(nv)}</b> <span style="color:${col}">(${sign}${fmtVal(delta)})</span></div>`;
                html += `</div>`;

                // Consolidation impact: show all ZZZZZZ_ rows where previousValuePlanningValues differs from current.
                const consolRows = state.consolidation || [];
                if (consolRows.length && typeof previousValuePlanningValues !== 'undefined') {
                    const periods = state.periods || [];
                    const impactLines = [];
                    for (const crow of consolRows) {
                        const consolMat = String(crow.material_number || '');
                        if (!consolMat.startsWith('ZZZZZZ_')) continue;
                        const consolAux = crow.aux_column || '';
                        const rowKey = `13. Consolidation||${consolMat}||${consolAux}||`;
                        let totalDelta = 0;
                        for (const p of periods) {
                            const prev = previousValuePlanningValues[`${rowKey}${p}`];
                            if (prev === undefined) continue;
                            const cur = Number(crow.values[p] || 0);
                            totalDelta += cur - prev;
                        }
                        if (Math.abs(totalDelta) > 0.001) {
                            const label = consolMat.replace('ZZZZZZ_', '');
                            const sign2 = totalDelta >= 0 ? '+' : '';
                            const col2  = totalDelta >= 0 ? '#4ade80' : '#f87171';
                            impactLines.push(`<div style="display:flex;justify-content:space-between;gap:12px;font-size:10px;padding:1px 0">` +
                                `<span style="color:#94a3b8">${label}</span>` +
                                `<span style="color:${col2};font-weight:600">${sign2}${fmtVal(totalDelta)}</span></div>`);
                        }
                    }
                    if (impactLines.length) {
                        html += `<div style="margin-top:6px;padding:5px 7px;background:rgba(255,255,255,0.06);border-radius:5px">`;
                        html += `<div style="color:#fde68a;font-weight:600;font-size:10px;margin-bottom:4px">&#8594; Impact Financial Consolidation (totaal alle perioden)</div>`;
                        html += impactLines.join('');
                        html += `</div>`;
                    }
                }
            }
        }

        return html;
    }
    if (sheetName === 'values' && type === 'start') {
        const rawStart = valueRow ? Number(valueRow.starting_stock || 0) : 0;
        const rowInfo = VALUE_ROW_INFO[lt] || {};
        let html = `<div class="tt-title">${lt} - Starting Stock</div>`;
        html += `<div style="color:#cbd5e1;margin-bottom:4px">${rowInfo.startDesc || 'Startbedrag waarmee deze Value Planning rij de eerste periode ingaat.'}</div>`;
        html += `<div class="tt-calc">Startwaarde: ${fmtVal(rawStart)}</div>`;
        if (VALUE_AUX_INFO[lt] && VALUE_AUX_INFO[lt].aux1) {
            html += `<div class="tt-aux">Driver: ${rowInfo.driverLabel || 'Aux 1'} - ${VALUE_AUX_INFO[lt].aux1}</div>`;
        }
        return html;
    }
    if (sheetName === 'values' && type === 'val') {
        if (String(mat || '').startsWith('ZZZZZZ_')) {
            return _buildVpConsolTooltipHtml(mat, period);
        }
        const rawVal = valueRow && period ? Number(valueRow.values[period] || 0) : 0;
        const aux1 = valueRow && valueRow.aux_column != null ? fmtVal(Number(valueRow.aux_column)) : '-';
        const aux2raw = valueRow && valueRow.aux_2_column != null ? String(valueRow.aux_2_column) : '';
        const aux2 = aux2raw ? aux2raw.replace(/!$/, '') : '-';
        const cascOrig = cellCtx ? cellCtx.ttCascOrig : undefined;
        const cascNew = cellCtx ? cellCtx.ttCascNew : undefined;
        const cascDelta = cellCtx ? Number(cellCtx.ttCascDelta || 0) : 0;
        const rowInfo = VALUE_ROW_INFO[lt] || {};
        let html = `<div class="tt-title">${rowInfo.noun || lt}${period ? ' - ' + period : ''}</div>`;
        html += `<div style="color:#cbd5e1;margin-bottom:4px">${rowInfo.desc || 'Berekend bedrag voor deze Value Planning rij en periode.'}</div>`;
        html += `<div class="tt-calc">${rowInfo.valueLabel || 'Bedrag'}${period ? ' in ' + period : ''}: ${fmtVal(rawVal)}</div>`;
        if (cascOrig !== undefined && cascNew !== undefined) {
            const sign = cascDelta >= 0 ? '+' : '';
            html += `<div class="tt-calc">Cascade: was ${cascOrig}, nu ${cascNew} (${sign}${fmtVal(cascDelta)})</div>`;
            html += `<div class="tt-aux">Gevolg: deze cel wijzigde automatisch door herberekening (planning/aux wijziging), niet door directe celinvoer.</div>`;
        }
        const qty = _getPlanQuantityForValueRow(valueRow, period, rowCtx);
        const factor = Number(valueRow && valueRow.aux_column || 0);
        if (qty !== null && Number.isFinite(factor)) {
            const qtyLabel = rowInfo.calcLabel || 'volume';
            const driverLabel = rowInfo.driverLabel || 'Aux 1';
            html += `<div class="tt-calc">Berekening: ${qtyLabel} ${Number(qty).toLocaleString(undefined, {maximumFractionDigits:1})} x ${driverLabel} ${fmtVal(factor)} = ${fmtVal(rawVal)}</div>`;
        }
        html += `<div class="tt-aux">${rowInfo.driverLabel || 'Aux 1'}: ${aux1}<br>Aux 2: ${aux2}</div>`;
        if (VALUE_AUX_INFO[lt] && VALUE_AUX_INFO[lt].aux1) {
            html += `<div class="tt-aux" style="margin-top:6px">Uitleg: ${VALUE_AUX_INFO[lt].aux1}</div>`;
        }
        return html;
    }
    if (type === 'val') {
        // Show colored dep indicators in the calc line when deps are highlighted
        const deps = info ? (info.deps || []) : [];
        const depColorMap = {}; // lt â†’ colorIdx
        deps.forEach((d, i) => { depColorMap[d] = i + 1; });

        let html = `<div class="tt-title">${lt}${period ? ' - ' + period : ''}`;
        // Dep color legend dots
        if (deps.length > 0) {
            html += ' <span style="font-weight:normal;font-size:10px">';
            deps.forEach((d, i) => {
                const col = DEP_COLORS[i + 1] || DEP_COLORS[DEP_COLORS.length - 1];
                html += `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${col.border};margin:0 2px;vertical-align:middle" title="${d}"></span>`;
            });
            html += '</span>';
        }
        html += '</div>';

        if (info && info.desc) html += `<div style="color:#94a3b8;font-size:10px;margin-bottom:5px">${info.desc}</div>`;

        const calcLine = period ? _getLtCalcExample(lt, mat, period, rowCtx) : null;
        if (calcLine) {
            // Color-code values using depsHighlight order so colors match the row highlights
            const depsHL = info && info.depsHighlight
                ? info.depsHighlight
                : deps.map(d => ({ lt: d, periodOffset: 0 }));
            let coloredLine = calcLine;
            // Resolve lead time once if needed
            let _ttLeadTime = 0;
            if (depsHL.some(d => d.useLeadTime) && state.results) {
                const lt07rows = state.results['07. Purchase plan'] || [];
                const lt07row = lt07rows.find(r => r.material_number === mat);
                _ttLeadTime = lt07row ? Math.round(Number(lt07row.aux_column) || 0) : 0;
            }
            const _ttPeriods = state.periods || [];
            const _ttPIdx = _ttPeriods.indexOf(period);
            depsHL.forEach((d, i) => {
                const col = DEP_COLORS[i + 1] || DEP_COLORS[DEP_COLORS.length - 1];
                if (!state.results) return;
                // Resolve the target period for this dep
                let tPeriod = period;
                if (d.useLeadTime) {
                    const tIdx = _ttPIdx + _ttLeadTime;
                    tPeriod = (tIdx >= 0 && tIdx < _ttPeriods.length) ? _ttPeriods[tIdx] : null;
                } else if (d.periodOffset && d.periodOffset !== 0) {
                    const tIdx = _ttPIdx + d.periodOffset;
                    tPeriod = (tIdx >= 0 && tIdx < _ttPeriods.length) ? _ttPeriods[tIdx] : null;
                }
                let val = null;
                if (d.lt === lt && d.periodOffset < 0) {
                    // Self-reference previous period (e.g. inventory m-1)
                    const rows = state.results[lt] || [];
                    const row = _pickRowForContext(rows, mat, rowCtx);
                    if (row) {
                        val = tPeriod ? (row.values[tPeriod] ?? 0) : (row.starting_stock || 0);
                    }
                } else if (tPeriod) {
                    const rows = state.results[d.lt] || [];
                    const row = _pickRowForContext(rows, mat, d.lt === lt ? rowCtx : null);
                    if (row) val = row.values[tPeriod] ?? 0;
                }
                if (val === null) return;
                const valStr = Number(val).toLocaleString(undefined, {maximumFractionDigits: 1});
                coloredLine = coloredLine.replace(
                    valStr,
                    `<span style="color:${col.text};font-weight:600">${valStr}</span>`
                );
            });
            html += `<div class="tt-calc">${coloredLine}</div>`;
        } else if (period && state.results) {
            const rows = state.results[lt] || [];
            const row = _pickRowForContext(rows, mat, rowCtx);
            const val = row ? (row.values[period] || 0) : 0;
            html += `<div class="tt-calc">${period}: ${Number(val).toLocaleString(undefined, {maximumFractionDigits:1})}</div>`;
        }
        if (info && info.formula) {
            _renderFormula(lt).split('\n').slice(0, 3).forEach(l => { html += `<span class="tt-formula">${l}</span>`; });
        }
        if (deps.length > 0) {
            html += _isDepHighlightEnabled()
                ? `<div class="tt-aux" style="margin-top:6px">Klik om afhankelijke cellen te markeren</div>`
                : `<div class="tt-aux" style="margin-top:6px">Markering staat uit. Zet Markering aan om afhankelijke cellen te kleuren.</div>`;
        }
        return html;
    }
    return `<div class="tt-title">${lt}</div>`;
}

function _getConsolValue(materialKey, period) {
    if (!period || !state || !state.consolidation) return null;
    const row = state.consolidation.find(r => r.material_number === materialKey);
    if (!row || !row.values) return null;
    return Number(row.values[period] || 0);
}

function _getPlanQuantityForValueRow(valueRow, period, rowCtx) {
    if (!valueRow || !period || !state || !state.results) return null;
    const info = VALUE_ROW_INFO[valueRow.line_type];
    if (!info || !info.calcLt) return null;
    const planRows = state.results[info.calcLt] || [];
    const planRow = _pickRowForContext(planRows, valueRow.material_number, rowCtx)
        || _pickRowForContext(planRows, valueRow.material_number, null);
    if (!planRow || !planRow.values) return null;
    const qty = Number(planRow.values[period] || 0);
    return Number.isFinite(qty) ? qty : null;
}

const VP_CONSOL_DEPS = {
    'ZZZZZZ_TURNOVER': { sourceLts: ['01. Demand forecast'], formula: 'Som van alle LT01 Demand forecast value-rijen' },
    'ZZZZZZ_RAW MATERIAL COST': { sourceLts: ['03. Total demand'], formula: 'Som van alle LT03 Total demand value-rijen' },
    'ZZZZZZ_MACHINE COST': { sourceLts: ['07. Capacity utilization'], formula: 'Som van alle LT07 Capacity utilization value-rijen' },
    'ZZZZZZ_DIRECT FTE COST': { sourceLts: ['12. FTE requirements'], formula: 'Som van alle LT12 FTE requirements value-rijen' },
    'ZZZZZZ_INDIRECT FTE COST': { fixed: true, formula: 'Vaste maandelijkse indirect FTE cost uit de valuation parameters' },
    'ZZZZZZ_OVERHEAD COST': { fixed: true, formula: 'Vaste maandelijkse overhead cost uit de valuation parameters' },
    'ZZZZZZ_COST OF GOODS': { consol: ['ZZZZZZ_RAW MATERIAL COST', 'ZZZZZZ_MACHINE COST', 'ZZZZZZ_DIRECT FTE COST', 'ZZZZZZ_INDIRECT FTE COST', 'ZZZZZZ_OVERHEAD COST'], formula: 'Raw Material + Machine + Direct FTE + Indirect FTE + Overhead' },
    'ZZZZZZ_GROSS MARGIN': { consol: ['ZZZZZZ_TURNOVER', 'ZZZZZZ_COST OF GOODS'], formula: 'Turnover - Cost of Goods' },
    'ZZZZZZ_SG&A COST': { fixed: true, formula: 'Vaste maandelijkse SG&A cost uit de valuation parameters' },
    'ZZZZZZ_EBITDA': { consol: ['ZZZZZZ_GROSS MARGIN', 'ZZZZZZ_SG&A COST'], formula: 'Gross Margin - SG&A Cost' },
    'ZZZZZZ_D&A COST': { fixed: true, formula: 'Depreciation per year / 12' },
    'ZZZZZZ_EBIT': { consol: ['ZZZZZZ_EBITDA', 'ZZZZZZ_D&A COST'], formula: 'EBITDA - D&A Cost' },
    'ZZZZZZ_FIXED ASSETS NET BOOK VALUE': { fixed: true, formula: 'Net book value uit valuation parameters' },
    'ZZZZZZ_INVENTORY VALUE': { sourceLts: ['04. Inventory'], formula: 'Som van alle LT04 Inventory value-rijen' },
    'ZZZZZZ_RECEIVABLES': { sourceLts: ['01. Demand forecast'], formula: 'Turnover x DSO / 30' },
    'ZZZZZZ_PAYABLES': { sourceLts: ['06. Purchase receipt'], formula: 'Purchase receipt x DPO / 30' },
    'ZZZZZZ_WORKING CAPITAL REQUIREMENTS': { consol: ['ZZZZZZ_RECEIVABLES', 'ZZZZZZ_INVENTORY VALUE', 'ZZZZZZ_PAYABLES'], formula: 'Receivables + Inventory Value - Payables' },
    'ZZZZZZ_CAPITAL INVESTMENT': { consol: ['ZZZZZZ_FIXED ASSETS NET BOOK VALUE', 'ZZZZZZ_WORKING CAPITAL REQUIREMENTS'], formula: 'Fixed Assets Net Book Value + Working Capital Requirements' },
    'ZZZZZZ_OPERATIONAL CASHFLOW': {
        consol: ['ZZZZZZ_EBITDA', 'ZZZZZZ_INVENTORY VALUE'],
        consolDeps: [
            { mat: 'ZZZZZZ_EBITDA', label: 'EBITDA' },
            { mat: 'ZZZZZZ_INVENTORY VALUE', label: 'Inventory Value' },
            { mat: 'ZZZZZZ_INVENTORY VALUE', periodOffset: -1, label: 'Inventory Value', fallbackSourceLts: ['04. Inventory'] },
        ],
        formula: 'EBITDA - Inventory Value huidige maand + Inventory Value vorige maand',
    },
    'ZZZZZZ_ROCE': { consol: ['ZZZZZZ_EBIT', 'ZZZZZZ_CAPITAL INVESTMENT'], formula: '(EBIT x 12) / Capital Investment' },
};

const VP_CONSOL_AUX_INFO = {
    'ZZZZZZ_TURNOVER': {
        aux1: 'Aux 1: bronlijn 01. Demand forecast; omzet = forecastvolume x gemiddelde verkoopprijs.',
        aux2: 'Aux 2: som van de omzet over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_RAW MATERIAL COST': {
        aux1: 'Aux 1: bronlijn 03. Total demand; grondstofkost = vraag x grondstofkost per eenheid.',
        aux2: 'Aux 2: som van de raw material cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_MACHINE COST': {
        aux1: 'Aux 1: bronlijn 07. Capacity utilization; machinekost = machine-uren x kost per machine-uur.',
        aux2: 'Aux 2: som van de machine cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_DIRECT FTE COST': {
        aux1: 'Aux 1: bronlijn 12. FTE requirements; directe personeelskost = FTE x direct FTE cost per maand.',
        aux2: 'Aux 2: som van de direct FTE cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_INDIRECT FTE COST': {
        aux1: 'Aux 1: geen bronlijn; vaste maandelijkse valuation parameter.',
        aux2: 'Aux 2: som van de indirect FTE cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_OVERHEAD COST': {
        aux1: 'Aux 1: geen bronlijn; vaste maandelijkse valuation parameter.',
        aux2: 'Aux 2: som van de overhead cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_COST OF GOODS': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld uit raw material, machine, direct FTE, indirect FTE en overhead.',
        aux2: 'Aux 2: som van Cost of Goods over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_GROSS MARGIN': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld als Turnover min Cost of Goods.',
        aux2: 'Aux 2: som van Gross Margin over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_SG&A COST': {
        aux1: 'Aux 1: geen bronlijn; vaste maandelijkse SG&A valuation parameter.',
        aux2: 'Aux 2: som van SG&A cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_EBITDA': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld als Gross Margin min SG&A cost.',
        aux2: 'Aux 2: som van EBITDA over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_D&A COST': {
        aux1: 'Aux 1: geen bronlijn; depreciation per year gedeeld door 12.',
        aux2: 'Aux 2: som van D&A cost over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_EBIT': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld als EBITDA min D&A cost.',
        aux2: 'Aux 2: som van EBIT over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_FIXED ASSETS NET BOOK VALUE': {
        aux1: 'Aux 1: geen bronlijn; vaste net book value valuation parameter.',
        aux2: 'Aux 2: gemiddelde Fixed Assets Net Book Value over de planningshorizon.',
    },
    'ZZZZZZ_INVENTORY VALUE': {
        aux1: 'Aux 1: bronlijn 04. Inventory; voorraadwaarde = voorraad x voorraadwaardering per eenheid.',
        aux2: 'Aux 2: gemiddelde Inventory Value inclusief Starting Stock en alle periodewaarden.',
    },
    'ZZZZZZ_RECEIVABLES': {
        aux1: 'Aux 1: bronlijn 01. Demand forecast; receivables = Turnover x DSO / 30.',
        aux2: 'Aux 2: gemiddelde Receivables over de planningshorizon.',
    },
    'ZZZZZZ_PAYABLES': {
        aux1: 'Aux 1: bronlijn 06. Purchase receipt; payables = Purchase receipt value x DPO / 30.',
        aux2: 'Aux 2: gemiddelde Payables over de planningshorizon.',
    },
    'ZZZZZZ_WORKING CAPITAL REQUIREMENTS': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld als Receivables + Inventory Value - Payables.',
        aux2: 'Aux 2: gemiddelde Working Capital Requirements over de planningshorizon.',
    },
    'ZZZZZZ_CAPITAL INVESTMENT': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld als Fixed Assets Net Book Value + Working Capital Requirements.',
        aux2: 'Aux 2: gemiddelde Capital Investment over de planningshorizon.',
    },
    'ZZZZZZ_OPERATIONAL CASHFLOW': {
        aux1: 'Aux 1: geen enkele bronlijn; samengesteld uit EBITDA en de maand-op-maand verandering in Inventory Value.',
        aux2: 'Aux 2: som van Operational Cashflow over alle periodes in de planningshorizon.',
    },
    'ZZZZZZ_ROCE': {
        aux1: 'Aux 1: geen enkele bronlijn; ROCE wordt berekend uit EBIT en Capital Investment.',
        aux2: 'Aux 2: ratio van totale EBIT gedeeld door gemiddelde Capital Investment; dit is geen gewone som of gemiddelde.',
    },
};

function _getVpConsolAuxText(mat, type) {
    const info = VP_CONSOL_AUX_INFO[mat];
    if (info && info[type]) return info[type];
    return type === 'aux1'
        ? 'Aux 1: bronrij of business driver die deze consolidatieregel voedt.'
        : 'Aux 2: som, gemiddelde of ratio die hoort bij deze specifieke consolidatieregel.';
}

const FINANCE_CONSOL_MAT = {
    turnover: 'ZZZZZZ_TURNOVER',
    raw_material_cost: 'ZZZZZZ_RAW MATERIAL COST',
    machine_cost: 'ZZZZZZ_MACHINE COST',
    direct_fte_cost: 'ZZZZZZ_DIRECT FTE COST',
    indirect_fte_cost: 'ZZZZZZ_INDIRECT FTE COST',
    overhead_cost: 'ZZZZZZ_OVERHEAD COST',
    cost_of_goods: 'ZZZZZZ_COST OF GOODS',
    gross_margin: 'ZZZZZZ_GROSS MARGIN',
    sga_cost: 'ZZZZZZ_SG&A COST',
    ebitda: 'ZZZZZZ_EBITDA',
    da_cost: 'ZZZZZZ_D&A COST',
    ebit: 'ZZZZZZ_EBIT',
    fixed_assets_nbv: 'ZZZZZZ_FIXED ASSETS NET BOOK VALUE',
    inventory_value: 'ZZZZZZ_INVENTORY VALUE',
    receivables: 'ZZZZZZ_RECEIVABLES',
    payables: 'ZZZZZZ_PAYABLES',
    working_capital: 'ZZZZZZ_WORKING CAPITAL REQUIREMENTS',
    capital_investment: 'ZZZZZZ_CAPITAL INVESTMENT',
    operational_cashflow: 'ZZZZZZ_OPERATIONAL CASHFLOW',
    roce: 'ZZZZZZ_ROCE',
    avg_monthly_turnover: 'ZZZZZZ_TURNOVER',
    avg_monthly_ebitda: 'ZZZZZZ_EBITDA',
    avg_monthly_ebit: 'ZZZZZZ_EBIT',
    avg_roce: 'ZZZZZZ_ROCE',
};

function _getFinanceConsolMaterial(key) {
    return FINANCE_CONSOL_MAT[key] || null;
}

function _getVpConsolRow(mat) {
    const rows = (state.valueResults && state.valueResults['13. Consolidation']) || [];
    return rows.find(r => r.material_number === mat) || null;
}

function _normalizeConsolDeps(spec) {
    if (!spec) return [];
    if (Array.isArray(spec.consolDeps)) {
        return spec.consolDeps.map((d, i) => ({
            mat: d.mat,
            periodOffset: Number(d.periodOffset || 0),
            label: d.label || String(d.mat || '').replace('ZZZZZZ_', ''),
            fallbackSourceLts: d.fallbackSourceLts || [],
            colorIdx: i + 1,
        })).filter(d => d.mat);
    }
    return (spec.consol || []).map((mat, i) => ({
        mat,
        periodOffset: 0,
        label: String(mat || '').replace('ZZZZZZ_', ''),
        fallbackSourceLts: [],
        colorIdx: i + 1,
    }));
}

function _resolvePeriodOffset(period, offset) {
    if (!period || !offset) return period || null;
    const periods = (state && state.periods) || [];
    const idx = periods.indexOf(period);
    if (idx < 0) return null;
    const targetIdx = idx + offset;
    return targetIdx >= 0 && targetIdx < periods.length ? periods[targetIdx] : null;
}

function _buildVpConsolTooltipHtml(mat, period) {
    const row = _getVpConsolRow(mat);
    const spec = VP_CONSOL_DEPS[mat] || {};
    const label = String(mat || '').replace('ZZZZZZ_', '');
    const value = row && period ? Number(row.values[period] || 0) : 0;
    let html = `<div class="tt-title">${label}${period ? ' - ' + period : ''}</div>`;
    html += `<div style="color:#cbd5e1;margin-bottom:4px">${FINANCE_KPI_INFO[Object.keys(FINANCE_CONSOL_MAT).find(k => FINANCE_CONSOL_MAT[k] === mat)]?.text || 'Samengestelde P&L / ROCE regel in Values Planning.'}</div>`;
    html += `<span class="tt-formula">${spec.formula || 'Specifieke P&L / ROCE berekening'}</span>`;
    if (period) html += `<div class="tt-calc">Waarde: ${fmtVal(value)}</div>`;
    if (spec.sourceLts && spec.sourceLts.length) {
        html += `<div class="tt-aux">Klik om alle onderliggende ${spec.sourceLts.join(', ')} rijen in Values Planning te markeren.</div>`;
    } else if (spec.consol && spec.consol.length) {
        const depLabels = _normalizeConsolDeps(spec).map(dep => {
            const offsetTxt = dep.periodOffset < 0 ? ' (vorige maand)' : dep.periodOffset > 0 ? ' (volgende maand)' : '';
            return `${dep.label}${offsetTxt}`;
        });
        html += `<div class="tt-aux">Klik om de gebruikte ZZZZZZ-bronrijen te markeren: ${depLabels.join(', ')}.</div>`;
    } else if (spec.fixed) {
        html += `<div class="tt-aux">Deze rij komt uit vaste valuation parameters; er zijn geen materiaalrijen om te markeren.</div>`;
    }
    return html;
}

function _buildFinanceTooltipHtml(key, period) {
    const info = FINANCE_KPI_INFO[key];
    if (!info) return '';
    const consolMat = _getFinanceConsolMaterial(key);
    let html = `<div class="tt-title">${info.title}</div>`;
    html += `<div style="color:#cbd5e1;margin-bottom:4px">${info.text}</div>`;
    html += `<div class="tt-aux">${info.drivers}</div>`;
    if (period) {
        const v = (k) => _getConsolValue(k, period);
        const f = (n) => (n == null ? '-' : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));
        let formulaLine = '';
        if (key === 'cost_of_goods') {
            formulaLine = `COGS = ${f(v('ZZZZZZ_RAW MATERIAL COST'))} + ${f(v('ZZZZZZ_MACHINE COST'))} + ${f(v('ZZZZZZ_DIRECT FTE COST'))} + ${f(v('ZZZZZZ_INDIRECT FTE COST'))} + ${f(v('ZZZZZZ_OVERHEAD COST'))}`;
        } else if (key === 'gross_margin') {
            formulaLine = `Gross Margin = ${f(v('ZZZZZZ_TURNOVER'))} - ${f(v('ZZZZZZ_COST OF GOODS'))}`;
        } else if (key === 'ebitda') {
            formulaLine = `EBITDA = ${f(v('ZZZZZZ_GROSS MARGIN'))} - ${f(v('ZZZZZZ_SG&A COST'))}`;
        } else if (key === 'ebit') {
            formulaLine = `EBIT = ${f(v('ZZZZZZ_EBITDA'))} - ${f(v('ZZZZZZ_D&A COST'))}`;
        } else if (key === 'working_capital') {
            formulaLine = `Working Capital = ${f(v('ZZZZZZ_RECEIVABLES'))} + ${f(v('ZZZZZZ_INVENTORY VALUE'))} - ${f(v('ZZZZZZ_PAYABLES'))}`;
        } else if (key === 'capital_investment') {
            formulaLine = `Capital Investment = ${f(v('ZZZZZZ_FIXED ASSETS NET BOOK VALUE'))} + ${f(v('ZZZZZZ_WORKING CAPITAL REQUIREMENTS'))}`;
        } else if (key === 'roce') {
            formulaLine = `ROCE = (${f(v('ZZZZZZ_EBIT'))} x 12) / ${f(v('ZZZZZZ_CAPITAL INVESTMENT'))}`;
        } else if (key === 'operational_cashflow') {
            const prevPeriod = _resolvePeriodOffset(period, -1);
            const prevInv = prevPeriod ? f(_getConsolValue('ZZZZZZ_INVENTORY VALUE', prevPeriod)) : 'Inventory starting stock';
            formulaLine = `Operational Cashflow = ${f(v('ZZZZZZ_EBITDA'))} - ${f(v('ZZZZZZ_INVENTORY VALUE'))} + ${prevInv}`;
        } else if (key === 'turnover') {
            formulaLine = `Turnover = LT01 volume x verkoopprijs (${period})`;
        }
        if (formulaLine) {
            html += `<div class="tt-formula" style="margin-top:6px">Periode ${period}: ${formulaLine}</div>`;
        }
    }
    if (consolMat) {
        const spec = VP_CONSOL_DEPS[consolMat] || {};
        const clickText = period
            ? 'Klik om in de Values Planning tabel eronder alleen de termen van deze berekening te tonen en te markeren.'
            : 'Klik om in de Values Planning tabel eronder alleen de termen van deze berekening te tonen en te markeren.';
        html += `<div class="tt-aux" style="margin-top:6px">${clickText}</div>`;
        if (spec.sourceLts && spec.sourceLts.length) {
            html += `<div class="tt-aux">Bron: ${spec.sourceLts.join(', ')}</div>`;
        } else if (spec.consol && spec.consol.length) {
            const depLabels = _normalizeConsolDeps(spec).map(dep => {
                const offsetTxt = dep.periodOffset < 0 ? ' vorige maand' : dep.periodOffset > 0 ? ' volgende maand' : ' huidige maand';
                return `${dep.label} (${offsetTxt})`;
            });
            html += `<div class="tt-aux">Bronnen: ${depLabels.join(', ')}</div>`;
        }
    }
    return html;
}

// ===== DEPENDENCY HIGHLIGHTING =====
function _clearDepHighlight() {
    const hadVpBreakdown = vpConsolBreakdownActive;
    vpConsolBreakdownActive = false;
    _markTooltipSourceCell(null);
    // Remove inline background from every td in every highlighted row
    document.querySelectorAll('#planBody tr.dep-hl, #vpBody tr.dep-hl').forEach(row => {
        row.classList.remove('dep-hl');
        Array.from(row.cells).forEach(td => {
            td.style.removeProperty('background-color');
        });
    });
    // Defensive cleanup for stale inline dependency styles after rerenders or mode switches.
    document.querySelectorAll('#planBody td[style*="background-color"], #vpBody td[style*="background-color"]').forEach(td => {
        td.style.removeProperty('background-color');
    });
    // Remove inline outline from period cells
    document.querySelectorAll('#planBody td.dep-cell-hl, #vpBody td.dep-cell-hl').forEach(c => {
        c.classList.remove('dep-cell-hl');
        c.style.removeProperty('outline');
        c.style.removeProperty('outline-offset');
    });
    document.querySelectorAll('#planBody td[style*="outline"], #vpBody td[style*="outline"]').forEach(c => {
        if (c.classList.contains('cell-active') || c.classList.contains('cell-range-selected-1') || c.classList.contains('cell-range-selected-2')) return;
        c.style.removeProperty('outline');
        c.style.removeProperty('outline-offset');
    });
    activeDepHighlight = null;
    if (hadVpBreakdown && typeof filterVPTable === 'function') {
        filterVPTable();
    }
}

function resetPlanningInteractionModes() {
    editMode = false;
    tooltipsMode = false;
    cellMarkMode = false;
        document.body.classList.remove('edit-mode', 'tooltips-mode', 'cell-mark-mode');
    document.querySelectorAll('.edit-mode-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.innerHTML = '&#9998; Bewerken';
    });
    document.querySelectorAll('.tooltips-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.innerHTML = '&#128712; Tooltips';
    });
    document.querySelectorAll('.dep-highlight-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.innerHTML = '&#9679; Markering';
    });
    const active = document.querySelector('#planBody td.editable-cell[contenteditable="true"], #vpBody td.editable-cell[contenteditable="true"]');
    if (active) active.blur();
    const tip = document.getElementById('ltInfoTooltip');
    if (tip) tip.style.display = 'none';
    const smallTip = document.getElementById('planTooltip');
    if (smallTip) smallTip.style.display = 'none';
    _clearDepHighlight();
}

function _applyDepHighlight(lt, mat, period) {
    if (!tooltipsMode) {
        _clearDepHighlight();
        return;
    }
    _clearDepHighlight();
    const info = LT_INFO[lt];
    const deps = info ? (info.deps || []) : [];

    // Build highlight items: use depsHighlight if defined, else fall back to deps with offset 0
    const rawDepsHL = info && info.depsHighlight
        ? info.depsHighlight
        : deps.map(d => ({ lt: d, periodOffset: 0 }));

    // Resolve lead time for this material if needed (LT07 Purchase plan)
    let leadTime = 0;
    if (rawDepsHL.some(d => d.useLeadTime)) {
        const lt07rows = (state.results && state.results['07. Purchase plan']) || [];
        const lt07row = lt07rows.find(r => r.material_number === mat);
        leadTime = lt07row ? Math.round(Number(lt07row.aux_column) || 0) : 0;
    }

    const periods = (state && state.periods) || [];

    // Helper: resolve the target period given a base period and an offset
    const resolvePeriod = (base, offset) => {
        if (!base || offset === 0) return base;
        const idx = periods.indexOf(base);
        if (idx < 0) return null;
        const tIdx = idx + offset;
        return (tIdx >= 0 && tIdx < periods.length) ? periods[tIdx] : null;
    };

    // Source row (colorIdx 0) + dep rows (colorIdx 1..N)
    const allItems = [
        { lt, colorIdx: 0, targetPeriod: period },
        ...rawDepsHL.map((d, i) => {
            const offset = d.useLeadTime ? leadTime : (d.periodOffset || 0);
            return { lt: d.lt, colorIdx: i + 1, targetPeriod: resolvePeriod(period, offset) };
        }),
    ];

    let totalFound = 0;
    allItems.forEach(({ lt: rowLt, colorIdx, targetPeriod }) => {
        const color = DEP_COLORS[Math.min(colorIdx, DEP_COLORS.length - 1)];
        ['planBody', 'vpBody'].forEach(bodyId => {
            const body = document.getElementById(bodyId);
            if (!body) return;
            Array.from(body.rows).forEach(row => {
                if (row.dataset.material !== mat || row.dataset.linetype !== rowLt) return;
                totalFound++;
                row.classList.add('dep-hl');
                Array.from(row.cells).forEach(td => {
                    td.style.setProperty('background-color', color.bg, 'important');
                });
                // Outline the target period cell
                if (targetPeriod) {
                    const cell = Array.from(row.cells).find(c => c.dataset.period === targetPeriod);
                    if (cell) {
                        cell.classList.add('dep-cell-hl');
                        cell.style.setProperty('outline', `2px solid ${color.border}`, 'important');
                        cell.style.setProperty('outline-offset', '-1px');
                    }
                }
                // For the source row (colorIdx 0): outline aux cells that are used in the calculation
                if (colorIdx === 0) {
                    const srcAux = info ? (info.sourceAux || []) : [];
                    srcAux.forEach(tt => {
                        const cell = Array.from(row.cells).find(c => c.dataset.tt === tt);
                        if (cell) {
                            cell.classList.add('dep-cell-hl');
                            cell.style.setProperty('outline', `2px solid ${color.border}`, 'important');
                            cell.style.setProperty('outline-offset', '-1px');
                        }
                    });
                }
            });
        });
    });
    // General case: for any dep with a negative periodOffset that falls before the first period,
    // the starting_stock of that dep row is used instead â€” highlight it with the dep's color.
    rawDepsHL.forEach((d, i) => {
        const offset = d.periodOffset || 0;
        if (offset >= 0 || d.useLeadTime) return;      // only backward offsets
        const tIdx = periods.indexOf(period) + offset;
        if (tIdx >= 0) return;                          // resolved fine, no need for starting_stock fallback
        const depColor = DEP_COLORS[Math.min(i + 1, DEP_COLORS.length - 1)];
        ['planBody', 'vpBody'].forEach(bodyId => {
            const body = document.getElementById(bodyId);
            if (!body) return;
            Array.from(body.rows).forEach(row => {
                if (row.dataset.material !== mat || row.dataset.linetype !== d.lt) return;
                const startCell = Array.from(row.cells).find(c => c.dataset.tt === 'start');
                if (startCell) {
                    startCell.classList.add('dep-cell-hl');
                    startCell.style.setProperty('outline', `2px solid ${depColor.border}`, 'important');
                    startCell.style.setProperty('outline-offset', '-1px');
                }
            });
        });
    });

    activeDepHighlight = { mat, lt, period };
}

function _applyVpConsolHighlight(mat, period) {
    if (!tooltipsMode) {
        _clearDepHighlight();
        return;
    }
    _clearDepHighlight();
    const body = document.getElementById('vpBody');
    const spec = VP_CONSOL_DEPS[mat];
    if (!body || !spec) {
        activeDepHighlight = { mat, lt: '13. Consolidation', period };
        return;
    }
    vpConsolBreakdownActive = true;
    const consolDeps = _normalizeConsolDeps(spec);
    const fallbackSourceLtsNeeded = new Set();
    consolDeps.forEach(dep => {
        const targetPeriod = _resolvePeriodOffset(period, dep.periodOffset);
        if (period && targetPeriod === null) {
            (dep.fallbackSourceLts || []).forEach(lt => fallbackSourceLtsNeeded.add(lt));
        }
    });

    const isTermRow = (row) => {
        const rowMat = row.dataset.material || '';
        const rowLt = row.dataset.linetype || '';
        if (rowMat === mat) return true;
        if (spec.fixed) return false;
        if (spec.sourceLts && spec.sourceLts.includes(rowLt) && !rowMat.startsWith('ZZZZZZ_')) return true;
        if (consolDeps.some(dep => dep.mat === rowMat)) return true;
        if (fallbackSourceLtsNeeded.has(rowLt)) return true;
        return false;
    };

    const markRow = (row, colorIdx, targetPeriod = period, options = {}) => {
        const color = DEP_COLORS[Math.min(colorIdx, DEP_COLORS.length - 1)];
        row.classList.add('dep-hl');
        Array.from(row.cells).forEach(td => {
            td.style.setProperty('background-color', color.bg, 'important');
        });
        if (targetPeriod) {
            const cell = Array.from(row.cells).find(c => c.dataset.period === targetPeriod);
            if (cell) {
                cell.classList.add('dep-cell-hl');
                cell.style.setProperty('outline', `2px solid ${color.border}`, 'important');
                cell.style.setProperty('outline-offset', '-1px');
            }
        } else if (options.fallbackStart) {
            const cell = Array.from(row.cells).find(c => c.dataset.tt === 'start');
            if (cell) {
                cell.classList.add('dep-cell-hl');
                cell.style.setProperty('outline', `2px solid ${color.border}`, 'important');
                cell.style.setProperty('outline-offset', '-1px');
            }
        }
    };

    Array.from(body.rows).forEach(row => {
        const rowMat = row.dataset.material || '';
        const rowLt = row.dataset.linetype || '';
        if (row.dataset.material) row.style.display = isTermRow(row) ? '' : 'none';
        if (rowMat === mat) markRow(row, 0);
        if (!spec.fixed && spec.sourceLts && spec.sourceLts.includes(rowLt) && !rowMat.startsWith('ZZZZZZ_')) markRow(row, 1);
        if (!spec.fixed && consolDeps.length) {
            consolDeps.forEach(dep => {
                const targetPeriod = _resolvePeriodOffset(period, dep.periodOffset);
                if (rowMat === dep.mat) markRow(row, dep.colorIdx, targetPeriod);
                if (dep.fallbackSourceLts && dep.fallbackSourceLts.includes(rowLt) && targetPeriod === null) {
                    markRow(row, dep.colorIdx, null, { fallbackStart: true });
                }
            });
        }
    });
    const rowCountEl = document.getElementById('vpRowCount');
    if (rowCountEl) {
        rowCountEl.textContent = String(Array.from(body.rows).filter(row => row.dataset.material && row.style.display !== 'none').length);
    }
    document.getElementById('vpTableScroll')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    activeDepHighlight = { mat, lt: '13. Consolidation', period };
}

// ===== TOOLTIP + DEP DELEGATION (shared by planning + VP tables) =====
function _setupPlanTooltips(tbody) {
    const ltTip = document.getElementById('ltInfoTooltip');

    tbody.addEventListener('mouseover', function(e) {
        if (!tooltipsMode) return;
        const ttCell = e.target.closest('td[data-tt]');
        if (!ttCell) { if (ltTip) ltTip.style.display = 'none'; return; }
        // cursor is handled by CSS rule: body.tooltips-mode td[data-tt] { cursor: pointer }
        if (!ltTip) return;
        const rowEl = ttCell.closest('tr');
        const rowCtx = rowEl ? {
            aux: rowEl.dataset.aux || '',
            aux1: rowEl.dataset.aux || '',
            aux2: rowEl.dataset.aux2 || '',
        } : null;
        ltTip.innerHTML = _buildTooltipForCell(
            ttCell.dataset.tt,
            ttCell.dataset.lt,
            ttCell.dataset.mat || ttCell.dataset.matnum,
            ttCell.dataset.period,
            ttCell.dataset.sheet,
            rowCtx,
            ttCell.dataset
        );
        ltTip.style.display = 'block';
        _posTooltip(ltTip, e, { preferredSide: 'right' });
    });

    tbody.addEventListener('mousemove', function(e) {
        if (tooltipsMode && ltTip && ltTip.style.display !== 'none') _posTooltip(ltTip, e, { preferredSide: 'right' });
    });

    tbody.addEventListener('mouseout', function() {
        // cursor reset is handled by CSS â€” no JS manipulation needed
        if (ltTip) ltTip.style.display = 'none';
    });

    tbody.addEventListener('click', function(e) {
        if (!tooltipsMode) return;
        const ttCell = e.target.closest('td[data-tt]');
        if (!ttCell) { _clearDepHighlight(); return; }
        const lt     = ttCell.dataset.lt;
        const mat    = ttCell.dataset.mat || ttCell.dataset.matnum;
        const period = ttCell.dataset.period;
        const isVp = ttCell.closest && !!ttCell.closest('#vpBody');
        if (activeDepHighlight &&
            activeDepHighlight.mat === mat &&
            activeDepHighlight.lt  === lt  &&
            activeDepHighlight.period === period) {
            _clearDepHighlight();
        } else {
            if (isVp) {
                if (String(mat || '').startsWith('ZZZZZZ_')) {
                    _applyVpConsolHighlight(mat, period);
                    _markTooltipSourceCell(ttCell);
                } else {
                    _clearDepHighlight();
                    _markTooltipSourceCell(ttCell);
                    activeDepHighlight = { mat, lt, period, source: 'vp-cell' };
                }
            } else {
                _applyDepHighlight(lt, mat, period);
            }
        }
    });
}

// Called once from loadValuePlanningData() to set up VP tooltips + dep highlighting
function setupVPDelegation() {
    if (_vpDepSetup) return;
    const tbody = document.getElementById('vpBody');
    if (!tbody) return;
    _setupPlanTooltips(tbody);
    if (typeof _setupCopyOnDblClick === 'function' && !_vpCopyDelegationSetup) {
        _setupCopyOnDblClick(document.getElementById('vpHead'));
        _setupCopyOnDblClick(tbody);
        _vpCopyDelegationSetup = true;
    }
    tbody.addEventListener('click', function(e) {
        if (!editMode) return;
        const cell = e.target.closest('td.editable-cell[data-edit-kind="value-aux"]');
        if (!cell) return;
        if (cell.getAttribute('contenteditable') !== 'true') {
            const prev = document.querySelector('#planBody td.editable-cell[contenteditable="true"], #vpBody td.editable-cell[contenteditable="true"]');
            if (prev && prev !== cell) prev.blur();
            cell.contentEditable = 'true';
            cell.focus();
        }
    });
    tbody.addEventListener('focusin', function(e) {
        if (!editMode) return;
        const cell = e.target.closest('td.editable-cell[data-edit-kind="value-aux"]');
        if (!cell) return;
        const lt = cell.dataset.lt, mat = cell.dataset.mat;
        const rows = (state.valueResults && state.valueResults[lt]) || [];
        const row = rows.find(r => r.material_number === mat);
        const rawVal = row ? Number(row.aux_column || 0) : 0;
        if (!cell.hasAttribute('data-display-original')) cell.dataset.displayOriginal = cell.textContent;
        if (!cell.hasAttribute('data-original')) cell.dataset.original = String(rawVal);
        cell.textContent = typeof fmtFull === 'function'
            ? fmtFull(rawVal)
            : String(Math.round(rawVal * 1000) / 1000);
        const sel = window.getSelection(), range = document.createRange();
        range.selectNodeContents(cell); sel.removeAllRanges(); sel.addRange(range);
    });
    tbody.addEventListener('focusout', function(e) {
        const cell = e.target.closest('td.editable-cell[data-edit-kind="value-aux"][contenteditable="true"]');
        if (!cell) return;
        const origVal = cell.hasAttribute('data-original') ? parseFloat(cell.dataset.original) : NaN;
        cell.removeAttribute('contenteditable');
        cell.removeAttribute('data-original');
        delete cell.dataset.displayOriginal;
        if (!editMode || isNaN(origVal)) return;
        const newValue = parseFloat(cell.textContent.trim());
        if (isNaN(newValue) || Math.abs(newValue - origVal) < 0.0001) {
            cell.textContent = fmtVal(origVal);
            return;
        }
        _commitValueAuxEdit(cell, cell.dataset.lt, cell.dataset.mat, newValue, origVal);
    });
    tbody.addEventListener('keydown', function(e) {
        if (!editMode) return;
        const cell = e.target.closest('td.editable-cell[data-edit-kind="value-aux"]');
        if (!cell) return;
        if (e.key === 'Enter') {
            e.preventDefault();
            cell.blur();
        } else if (e.key === 'Escape') {
            cell.textContent = cell.dataset.displayOriginal || fmtVal(parseFloat(cell.dataset.original || '0'));
            cell.removeAttribute('data-original');
            delete cell.dataset.displayOriginal;
            cell.blur();
        }
    });
    _vpDepSetup = true;
}

function setupFinanceTooltips() {
    const valuesTab = document.getElementById('values-tab');
    const ltTip = document.getElementById('ltInfoTooltip');
    if (!valuesTab || !ltTip || valuesTab.dataset.finTooltipsBound === '1') return;

    valuesTab.addEventListener('mouseover', function(e) {
        if (!tooltipsMode) return;
        const el = e.target.closest('[data-fin-tt]');
        if (!el) return;
        el.removeAttribute('title');
        const html = _buildFinanceTooltipHtml(el.dataset.finTt, el.dataset.finPeriod || null);
        if (!html) return;
        ltTip.innerHTML = html;
        ltTip.style.display = 'block';
        _posTooltip(ltTip, e, { preferredSide: 'right' });
    });

    valuesTab.addEventListener('mousemove', function(e) {
        if (!tooltipsMode || !ltTip || ltTip.style.display === 'none') return;
        const el = e.target.closest('[data-fin-tt]');
        if (!el) return;
        _posTooltip(ltTip, e, { preferredSide: 'right' });
    });

    valuesTab.addEventListener('mouseout', function(e) {
        const el = e.target.closest('[data-fin-tt]');
        if (!el) return;
        if (ltTip) ltTip.style.display = 'none';
    });

    valuesTab.addEventListener('click', function(e) {
        if (!tooltipsMode) return;
        const el = e.target.closest('[data-fin-tt]');
        if (!el) return;
        const mat = _getFinanceConsolMaterial(el.dataset.finTt);
        if (!mat) return;
        const period = el.dataset.finPeriod || null;
        if (activeDepHighlight &&
            activeDepHighlight.mat === mat &&
            activeDepHighlight.lt === '13. Consolidation' &&
            activeDepHighlight.period === period) {
            _clearDepHighlight();
        } else {
            _applyVpConsolHighlight(mat, period);
            _markTooltipSourceCell(el);
        }
    });

    valuesTab.dataset.finTooltipsBound = '1';
}

window.addEventListener('DOMContentLoaded', setupFinanceTooltips);

async function _commitValueAuxEdit(cell, lineType, materialNumber, newValue, origVal) {
    cell.textContent = fmtVal(newValue);
    try {
        const res = await fetch('/api/update_value_aux', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ line_type: lineType, material_number: materialNumber, new_value: newValue })
        });
        const data = await res.json();
        if (data.error) {
            alert(data.error);
            cell.textContent = fmtVal(origVal);
            return;
        }
        state.valueResults = data.value_results;
        state.consolidation = data.consolidation || [];
        const key = `${lineType}||${materialNumber}`;
        const meta = data.edit_meta || {};
        if (meta.original_value !== undefined && Math.abs((meta.new_value ?? newValue) - meta.original_value) < 0.0001) {
            delete state.valueAuxEdits[key];
        } else {
            state.valueAuxEdits[key] = {
                original: meta.original_value ?? origVal,
                new: meta.new_value ?? newValue,
            };
        }
        loadValuePlanningData();
        renderConsolidation();
        renderFinChart();
        updateValueKPIs();
        if (state.results && typeof renderPlanningTable === 'function') renderPlanningTable(state.results, state.periods || []);
        else if (state.results && typeof updateTableFromResults === 'function') updateTableFromResults(state.results, null);
        state.dashboardDirty = true;
        if (typeof _markAutoSaved === 'function') _markAutoSaved();
    } catch (err) {
        alert('Error: ' + err);
        cell.textContent = fmtVal(origVal);
    }
}

