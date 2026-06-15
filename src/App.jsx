import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    Check,
    ChevronDown,
    Download,
    FileSpreadsheet,
    FileUp,
    Moon,
    RefreshCw,
    Search,
    SlidersHorizontal,
    Sun,
} from 'lucide-react';
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';

const API = import.meta.env.VITE_API_URL ||
    ((window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') && window.location.port !== '8000'
        ? 'http://127.0.0.1:8010'
        : window.location.origin);

const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'summary', label: 'Summary' },
    { id: 'schemes', label: 'Scheme Wise' },
    { id: 'archives', label: 'Archives' },
];

const tooltipCursor = { fill: 'var(--chart-cursor)' };
const activeBarStyle = {
    fill: 'var(--chart-active-bar)',
    stroke: 'var(--chart-active-stroke)',
    strokeWidth: 1,
};

function formatNumber(value, digits = 2) {
    if (typeof value !== 'number' || Number.isNaN(value)) return value ?? '';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: digits }).format(value);
}

function formatPercent(value) {
    if (typeof value !== 'number' || Number.isNaN(value)) return '-';
    return `${formatNumber(value * 100, 2)}%`;
}

function formatCrore(value) {
    if (typeof value !== 'number' || Number.isNaN(value)) return '0';
    return formatNumber(value, value >= 100000 ? 0 : 1);
}

function safeSeries(data, key) {
    return Array.isArray(data?.[key]) ? data[key] : [];
}

const categorySortOptions = [
    { value: 'aumShare', label: 'AUM Share: High to Low' },
    { value: 'latestAum', label: 'AUM: High to Low' },
    { value: 'marketShare', label: 'Market Share: High to Low' },
    { value: 'grossSales', label: 'Gross Sales: High to Low' },
    { value: 'netSales', label: 'Net Sales: High to Low' },
];

const schemeSortOptions = [
    { value: 'aum', label: 'AUM: High to Low' },
    { value: 'grossSales', label: 'Gross Sales: High to Low' },
    { value: 'netSales', label: 'Net Sales: High to Low' },
    { value: 'redemption', label: 'Redemption: High to Low' },
    { value: 'schemeName', label: 'Scheme Name: A to Z' },
];

function sortedRows(rows, key) {
    return [...rows].sort((a, b) => {
        const left = a?.[key];
        const right = b?.[key];
        if (typeof left === 'number' || typeof right === 'number') {
            const leftNumber = typeof left === 'number' && !Number.isNaN(left) ? left : Number.NEGATIVE_INFINITY;
            const rightNumber = typeof right === 'number' && !Number.isNaN(right) ? right : Number.NEGATIVE_INFINITY;
            return rightNumber - leftNumber;
        }
        return String(left || '').localeCompare(String(right || ''));
    });
}

function Section({ title, subtitle, headerAction, children, className = '' }) {
    return (
        <section className={`card ${className}`}>
            <div className="card-header">
                <div>
                    <h2>{title}</h2>
                    {subtitle && <p className="card-subtitle">{subtitle}</p>}
                </div>
                {headerAction && <div className="card-header-action">{headerAction}</div>}
            </div>
            <div className="card-body">{children}</div>
        </section>
    );
}

function MetricCard({ label, value, detail, tone = 'neutral' }) {
    return (
        <div className={`metric-card tone-${tone}`}>
            <span>{label}</span>
            <strong>{value}</strong>
            {detail && <small>{detail}</small>}
        </div>
    );
}

function EmptyState({ children = 'No data loaded.' }) {
    return <div className="empty-state">{children}</div>;
}

function GlassSelect({ icon, value, options, onChange }) {
    const [open, setOpen] = useState(false);
    const rootRef = useRef(null);
    const selected = options.find(option => option.value === value) || options[0];

    useEffect(() => {
        function handleClick(event) {
            if (!rootRef.current?.contains(event.target)) setOpen(false);
        }
        document.addEventListener('mousedown', handleClick);
        return () => document.removeEventListener('mousedown', handleClick);
    }, []);

    return (
        <div className="glass-select" ref={rootRef}>
            <button
                type="button"
                className={`glass-select-trigger ${open ? 'open' : ''}`}
                onClick={() => setOpen(value => !value)}
                aria-haspopup="listbox"
                aria-expanded={open}
            >
                {icon}
                <span>{selected?.label}</span>
                <ChevronDown size={16} className="glass-select-chevron" />
            </button>
            {open && (
                <div className="glass-select-menu" role="listbox">
                    {options.map(option => {
                        const active = option.value === value;
                        return (
                            <button
                                type="button"
                                key={option.value}
                                className={`glass-select-option ${active ? 'active' : ''}`}
                                role="option"
                                aria-selected={active}
                                onClick={() => {
                                    onChange(option.value);
                                    setOpen(false);
                                }}
                            >
                                <span>{option.label}</span>
                                {active && <Check size={15} />}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

function ChartTooltip({ active, payload, label }) {
    if (!active || !payload?.length) return null;
    const title = payload[0]?.payload?.tooltipLabel || payload[0]?.payload?.schemeName || payload[0]?.payload?.period || label;
    return (
        <div className="chart-tooltip">
            <strong>{title}</strong>
            {payload.map(item => (
                <span key={item.dataKey}>{item.name}: {formatNumber(item.value)}</span>
            ))}
        </div>
    );
}

function compactSchemeLabel(value) {
    const text = String(value || '')
        .replace(/^KOTAK\s+/i, 'K ')
        .replace(/\s*-\s*/g, ' - ')
        .replace(/\s+/g, ' ')
        .trim();
    return text.length > 32 ? `${text.slice(0, 29).trim()}...` : text;
}

function UploadControl({ loading, onUpload }) {
    const inputRef = useRef(null);
    const [fileName, setFileName] = useState('');

    async function submitUpload() {
        const file = inputRef.current?.files?.[0];
        if (!file) return;
        await onUpload(file);
        if (inputRef.current) inputRef.current.value = '';
        setFileName('');
    }

    return (
        <div className="upload-row compact-upload">
            <label className="file-input">
                <FileUp size={18} />
                <span>{fileName || 'Select weekly AMFI workbook'}</span>
                <input
                    ref={inputRef}
                    type="file"
                    accept=".xlsx,.xls"
                    onChange={event => setFileName(event.target.files?.[0]?.name || '')}
                />
            </label>
            <button className="btn-primary" onClick={submitUpload} disabled={loading || !fileName}>
                {loading ? <span className="spinner" /> : <FileUp size={18} />}
                Upload
            </button>
        </div>
    );
}

function DownloadActions({ selectedFY, selectedPeriodKey, selectedPeriodShort }) {
    if (!selectedFY) return null;
    const periodQuery = selectedPeriodKey ? `&period_key=${encodeURIComponent(selectedPeriodKey)}` : '';
    return (
        <div className="download-actions">
            <a className="download-button summary-download" href={`${API}/api/download-summary?financial_year=${selectedFY}${periodQuery}`}>
                <FileSpreadsheet size={18} />
                Summary: {selectedPeriodShort || 'Selected Period'}
            </a>
            <a className="download-button mom-download" href={`${API}/api/download-mom?financial_year=${selectedFY}`}>
                <Download size={18} />
                Full FY MoM YTD
            </a>
        </div>
    );
}

function PeriodSelector({ periods, selectedPeriodKey, onChange }) {
    if (!periods?.length) return null;
    return (
        <label className="period-selector">
            <span>Viewing month</span>
            <select value={selectedPeriodKey || periods[periods.length - 1]?.period_key || ''} onChange={event => onChange(event.target.value)}>
                {periods.map(period => (
                    <option key={period.period_key} value={period.period_key}>
                        {period.period_label}
                    </option>
                ))}
            </select>
        </label>
    );
}

function DashboardControls({
    loading,
    onUpload,
    onRefresh,
    archives,
    selectedFY,
    selectedPeriodKey,
    onPeriodChange,
    periods,
    selectedPeriodShort,
    showUpload = true,
    title,
    subtitle,
}) {
    return (
        <section className={`dashboard-controls ${showUpload ? '' : 'download-only-controls'}`} aria-label="Workbook controls">
            {(title || subtitle) && (
                <div className="control-copy">
                    {title && <h3>{title}</h3>}
                    {subtitle && <p>{subtitle}</p>}
                </div>
            )}
            {showUpload && <UploadControl loading={loading} onUpload={onUpload} />}
            <div className="download-control-row">
                {archives.length > 0 && (
                    <label className="control-field year-field">
                        <span>Financial year</span>
                        <select
                            className="fy-select"
                            value={selectedFY}
                            onChange={event => onRefresh(event.target.value, '')}
                        >
                            {archives.map(item => (
                                <option key={item.financial_year} value={item.financial_year}>
                                    FY {item.financial_year}
                                </option>
                            ))}
                        </select>
                    </label>
                )}
                <PeriodSelector
                    periods={periods}
                    selectedPeriodKey={selectedPeriodKey}
                    onChange={periodKey => onPeriodChange(periodKey)}
                />
                <div className="control-actions">
                    <DownloadActions
                        selectedFY={selectedFY}
                        selectedPeriodKey={selectedPeriodKey}
                        selectedPeriodShort={selectedPeriodShort}
                    />
                    <button className="btn-sm refresh-button" onClick={() => onRefresh(selectedFY, selectedPeriodKey)} disabled={loading} title="Refresh data">
                        <RefreshCw size={16} />
                    </button>
                </div>
            </div>
        </section>
    );
}

function Overview({ data, loading, onUpload, onRefresh, archives, selectedFY, selectedPeriodKey, onPeriodChange }) {
    const summary = data?.summary || {};
    const series = safeSeries(data, 'timeSeries');
    const selectedIndex = Math.max(0, series.findIndex(row => row.periodKey === selectedPeriodKey));
    const selectedPoint = series[selectedIndex] || series[series.length - 1] || {};
    const previous = selectedIndex > 0 ? series[selectedIndex - 1] : null;
    const aumGrowth = previous?.aum ? (selectedPoint.aum - previous.aum) / previous.aum : null;
    const periodText = data?.selectedPeriod || summary.latestPeriod || '-';
    const rangeText = data?.displayRange || '-';

    return (
        <>
            <DashboardControls
                loading={loading}
                onUpload={onUpload}
                onRefresh={onRefresh}
                archives={archives}
                selectedFY={selectedFY}
                selectedPeriodKey={selectedPeriodKey}
                onPeriodChange={onPeriodChange}
                periods={data?.periods || []}
                selectedPeriodShort={data?.selectedPeriodShort || data?.selectedPeriod}
            />
            <Section
                title="Executive Overview"
                subtitle={`FY ${selectedFY || '-'} | Viewing ${periodText} | Uploaded range ${rangeText}`}
            >
                {data?.warnings?.length ? (
                    <div className="warning-list">
                        {data.warnings.map((warning, index) => <span key={index}>{warning}</span>)}
                    </div>
                ) : null}
                <div className="metric-grid">
                    <MetricCard label="Selected Period" value={summary.latestPeriod || '-'} detail="Latest uploaded month" />
                    <MetricCard label="Total AUM" value={`${formatCrore(summary.latestAum)} Cr`} detail={`Vs previous period ${formatPercent(aumGrowth)}`} tone={typeof aumGrowth === 'number' ? (aumGrowth >= 0 ? 'good' : 'soft') : 'neutral'} />
                    <MetricCard label="Gross Sales" value={`${formatCrore(summary.latestGrossSales)} Cr`} detail={periodText} />
                    <MetricCard label="Net Sales" value={`${formatCrore(summary.latestNetSales)} Cr`} detail={periodText} tone={summary.latestNetSales >= 0 ? 'good' : 'soft'} />
                </div>
            </Section>

            <div className="dashboard-grid two-col">
                <Section title="AUM Trend" subtitle={`Chronological uploaded months: ${rangeText}`}>
                    {series.length ? (
                        <div className="chart-frame">
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={series} margin={{ top: 10, right: 36, left: 4, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                                    <XAxis dataKey="periodShort" stroke="var(--chart-axis)" tickMargin={10} interval={0} height={54} tick={{ fontSize: 12 }} />
                                    <YAxis stroke="var(--chart-axis)" tickMargin={10} width={78} tickFormatter={value => formatCrore(value)} />
                                    <Tooltip content={<ChartTooltip />} cursor={tooltipCursor} />
                                    <Area name="AUM" type="monotone" dataKey="aum" stroke="var(--chart-primary)" fill="var(--chart-fill)" strokeWidth={2.2} isAnimationActive={false} />
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    ) : <EmptyState />}
                </Section>

                <Section title="Flow Trend" subtitle={`Gross and net sales for uploaded months: ${rangeText}`}>
                    {series.length ? (
                        <div className="chart-frame">
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={series} margin={{ top: 10, right: 36, left: 4, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                                    <XAxis dataKey="periodShort" stroke="var(--chart-axis)" tickMargin={10} interval={0} height={54} tick={{ fontSize: 12 }} />
                                    <YAxis stroke="var(--chart-axis)" tickMargin={10} width={78} tickFormatter={value => formatCrore(value)} />
                                    <Tooltip content={<ChartTooltip />} cursor={tooltipCursor} />
                                    <Legend />
                                    <Line name="Gross Sales" type="monotone" dataKey="grossSales" stroke="var(--chart-primary)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
                                    <Line name="Net Sales" type="monotone" dataKey="netSales" stroke="var(--chart-secondary)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
                                </LineChart>
                            </ResponsiveContainer>
                        </div>
                    ) : <EmptyState />}
                </Section>
            </div>
        </>
    );
}

function SummaryView({ data }) {
    const categories = safeSeries(data, 'categorySummary');
    const [categorySort, setCategorySort] = useState('aumShare');
    const sortedCategories = useMemo(() => sortedRows(categories, categorySort), [categories, categorySort]);
    const totalAum = categories.reduce((sum, row) => sum + (row.latestAum || 0), 0);

    return (
        <>
            <Section
                title="Summary Rows"
                subtitle={`Summary sheet values for ${data?.selectedPeriod || '-'}`}
                headerAction={
                    <div className="table-controls">
                        <GlassSelect
                            icon={<SlidersHorizontal size={16} />}
                            value={categorySort}
                            options={categorySortOptions}
                            onChange={setCategorySort}
                        />
                    </div>
                }
            >
                {sortedCategories.length ? (
                    <div className="category-layout">
                        <div className="allocation-list">
                            {sortedCategories.map(row => (
                                <div className="allocation-row" key={row.category}>
                                    <div>
                                        <strong>{row.category}</strong>
                                        <span>{formatCrore(row.latestAum)} Cr AUM</span>
                                    </div>
                                    <div className="allocation-bar" aria-label={`${row.category} AUM share`}>
                                        <span style={{ width: `${Math.max((row.aumShare || 0) * 100, 2)}%` }} />
                                    </div>
                                    <em>{formatPercent(row.aumShare)}</em>
                                </div>
                            ))}
                        </div>
                        <div className="category-total">
                            <span>Total AUM</span>
                            <strong>{formatCrore(totalAum)} Cr</strong>
                            <small>{categories.length} categories</small>
                        </div>
                    </div>
                ) : <EmptyState />}
            </Section>

            <Section title="Summary Flow View" subtitle={`Gross Sales and Net Sales for ${data?.selectedPeriod || '-'}`}>
                {sortedCategories.length ? (
                    <div className="chart-frame tall">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={sortedCategories} margin={{ top: 10, right: 22, left: 6, bottom: 24 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                                <XAxis dataKey="category" stroke="var(--chart-axis)" interval={0} tickMargin={10} height={70} />
                                <YAxis stroke="var(--chart-axis)" tickFormatter={value => formatCrore(value)} width={78} />
                                <Tooltip content={<ChartTooltip />} cursor={tooltipCursor} />
                                <Legend />
                                <Bar name="Gross Sales" dataKey="grossSales" fill="var(--chart-primary)" activeBar={activeBarStyle} isAnimationActive={false} />
                                <Bar name="Net Sales" dataKey="netSales" fill="var(--chart-secondary)" activeBar={activeBarStyle} isAnimationActive={false} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                ) : <EmptyState />}
            </Section>

            <Section title="Summary Table" subtitle={`Current table reflects ${data?.selectedPeriod || '-'}`}>
                <SummaryTable
                    columns={[
                        ['category', 'Category'],
                        ['latestAum', 'Latest AUM'],
                        ['grossSales', 'Gross Sales'],
                        ['netSales', 'Net Sales'],
                        ['marketShare', 'AUM MS'],
                        ['aumShare', 'AUM Share'],
                    ]}
                    rows={sortedCategories}
                />
            </Section>
        </>
    );
}

function SchemesView({ data }) {
    const schemes = safeSeries(data, 'schemeSummary');
    const categories = useMemo(() => [...new Set(schemes.map(row => row.assetAmc).filter(Boolean))], [schemes]);
    const categoryOptions = useMemo(() => [
        { value: 'All', label: 'All AMC assets' },
        ...categories.map(item => ({ value: item, label: item })),
    ], [categories]);
    const [query, setQuery] = useState('');
    const [category, setCategory] = useState('All');
    const [schemeSort, setSchemeSort] = useState('aum');

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        return schemes.filter(row => {
            const matchesQuery = !q || `${row.schemeName} ${row.assetClass} ${row.assetAmc}`.toLowerCase().includes(q);
            const matchesCategory = category === 'All' || row.assetAmc === category;
            return matchesQuery && matchesCategory;
        });
    }, [schemes, query, category]);

    const sortedFiltered = useMemo(() => sortedRows(filtered, schemeSort), [filtered, schemeSort]);

    const topSchemes = useMemo(() => (
        sortedFiltered
            .filter(row => typeof row.aum === 'number')
            .sort((a, b) => (b.aum || 0) - (a.aum || 0))
            .slice(0, 10)
            .map(row => ({
                ...row,
                chartLabel: compactSchemeLabel(row.schemeName),
                tooltipLabel: row.schemeName,
            }))
    ), [sortedFiltered]);

    return (
        <>
            <Section
                title="Scheme Wise"
                subtitle={`${sortedFiltered.length} of ${schemes.length} schemes | ${data?.selectedPeriod || '-'}`}
                headerAction={
                    <div className="table-controls">
                        <label className="search-box">
                            <Search size={16} />
                            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search schemes" />
                        </label>
                        <GlassSelect
                            icon={<SlidersHorizontal size={16} />}
                            value={category}
                            options={categoryOptions}
                            onChange={setCategory}
                        />
                        <GlassSelect
                            icon={<SlidersHorizontal size={16} />}
                            value={schemeSort}
                            options={schemeSortOptions}
                            onChange={setSchemeSort}
                        />
                    </div>
                }
            >
                <SummaryTable
                    columns={[
                        ['schemeName', 'Scheme'],
                        ['assetClass', 'Asset Class'],
                        ['assetAmc', 'Asset AMC'],
                        ['aum', 'AUM'],
                        ['grossSales', 'Gross Sales'],
                        ['netSales', 'Net Sales'],
                        ['redemption', 'Redemption'],
                    ]}
                    rows={sortedFiltered}
                />
            </Section>

            <Section title="Top AUM Schemes" subtitle={`Top ${topSchemes.length} schemes by AUM for ${data?.selectedPeriod || '-'}`}>
                {topSchemes.length ? (
                    <div className="chart-frame scheme-chart">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={topSchemes} layout="vertical" margin={{ top: 8, right: 36, left: 12, bottom: 8 }} barCategoryGap={8}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" />
                                <XAxis type="number" stroke="var(--chart-axis)" tickFormatter={value => formatCrore(value)} />
                                <YAxis
                                    type="category"
                                    dataKey="chartLabel"
                                    stroke="var(--chart-axis)"
                                    width={230}
                                    interval={0}
                                    tick={{ fontSize: 12 }}
                                    tickLine={false}
                                />
                                <Tooltip content={<ChartTooltip />} cursor={tooltipCursor} />
                                <Bar name="AUM" dataKey="aum" fill="var(--chart-primary)" activeBar={activeBarStyle} isAnimationActive={false} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                ) : <EmptyState />}
            </Section>
        </>
    );
}

function SummaryTable({ columns, rows }) {
    if (!rows?.length) return <EmptyState>No rows available.</EmptyState>;
    return (
        <div className="table-scroll compact">
            <table className="theory-table">
                <thead>
                    <tr>
                        {columns.map(([, label]) => <th key={label}>{label}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, rowIndex) => (
                        <tr key={row.schemeKey || row.category || rowIndex}>
                            {columns.map(([key]) => {
                                const value = row[key];
                                const isPercent = key.toLowerCase().includes('share') || key.toLowerCase().includes('ms') || key.toLowerCase().includes('toaum');
                                const isNumeric = typeof value === 'number';
                                return (
                                    <td key={key} className={isNumeric ? 'numeric-cell' : undefined}>
                                        {isPercent ? formatPercent(value) : isNumeric ? formatNumber(value) : value}
                                    </td>
                                );
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function ArchivesView({ archives, loading, selectedFY, selectedPeriodKey, onRefresh, onPeriodChange, periods, selectedPeriodShort }) {
    return (
        <Section title="Archives" subtitle="Download generated month-level workbooks by financial year">
            {archives.length === 0 ? (
                <EmptyState>No archived financial years found.</EmptyState>
            ) : (
                <SummaryTable
                    columns={[
                        ['financial_year', 'Financial Year'],
                        ['period_count', 'Months'],
                        ['status', 'Status'],
                        ['last_modified', 'Last Modified'],
                    ]}
                    rows={archives.map(item => ({
                        ...item,
                        financial_year: `FY ${item.financial_year}`,
                        last_modified: item.last_modified ? new Date(item.last_modified).toLocaleString('en-IN') : '-',
                    }))}
                />
            )}
            <DashboardControls
                loading={loading}
                onRefresh={onRefresh}
                archives={archives}
                selectedFY={selectedFY}
                selectedPeriodKey={selectedPeriodKey}
                onPeriodChange={onPeriodChange}
                periods={periods}
                selectedPeriodShort={selectedPeriodShort}
                showUpload={false}
                title="Download Workbooks"
                subtitle="Summary uses the selected period. Full FY MoM/YTD downloads one block per uploaded month plus the final YTD block."
            />
            {loading && <div className="archive-actions"><span className="muted-text">Refreshing...</span></div>}
        </Section>
    );
}

export default function App() {
    const [activeTab, setActiveTab] = useState('overview');
    const [data, setData] = useState(null);
    const [archives, setArchives] = useState([]);
    const [selectedFY, setSelectedFY] = useState('');
    const [selectedPeriodKey, setSelectedPeriodKey] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [isDarkMode, setIsDarkMode] = useState(true);

    async function loadData(fy, periodKey = selectedPeriodKey) {
        setLoading(true);
        setError('');
        try {
            const params = new URLSearchParams();
            if (fy) params.set('financial_year', fy);
            if (periodKey) params.set('period_key', periodKey);
            const query = params.toString();
            const url = query ? `${API}/dashboard-data?${query}` : `${API}/dashboard-data`;
            const res = await fetch(url);
            const payload = await res.json();
            if (!res.ok) throw new Error(payload.detail || 'Unable to load dashboard data.');
            setData(payload);
            if (payload?.financialYear) setSelectedFY(payload.financialYear);
            if (payload?.selectedPeriodKey) setSelectedPeriodKey(payload.selectedPeriodKey);
        } catch (err) {
            setError(err.message || 'Unable to load dashboard data.');
        } finally {
            setLoading(false);
        }
    }

    async function loadArchives() {
        try {
            const res = await fetch(`${API}/api/archives`);
            if (!res.ok) return;
            const list = await res.json();
            setArchives(list);
            if (list.length > 0 && !selectedFY) {
                const latest = list[0].financial_year;
                setSelectedFY(latest);
                loadData(latest, '');
            }
        } catch (err) {
            console.error('Failed to load archives list:', err);
        }
    }

    async function uploadFile(file) {
        setLoading(true);
        setError('');
        const body = new FormData();
        body.append('file', file);
        try {
            const res = await fetch(`${API}/upload`, { method: 'POST', body });
            const payload = await res.json();
            if (!res.ok) throw new Error(payload.detail || 'Upload failed.');
            setData(payload);
            if (payload.financialYear) setSelectedFY(payload.financialYear);
            if (payload.selectedPeriodKey) setSelectedPeriodKey(payload.selectedPeriodKey);
            loadArchives();
        } catch (err) {
            setError(err.message || 'Upload failed.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        loadArchives().then(() => {
            if (!selectedFY) loadData();
        });
    }, []);

    const content = useMemo(() => {
        if (activeTab === 'overview') {
            return (
                <Overview
                    data={data}
                    loading={loading}
                    onUpload={uploadFile}
                    onRefresh={loadData}
                    archives={archives}
                    selectedFY={selectedFY}
                    selectedPeriodKey={selectedPeriodKey}
                    onPeriodChange={periodKey => loadData(selectedFY, periodKey)}
                />
            );
        }
        if (activeTab === 'summary') return <SummaryView data={data} />;
        if (activeTab === 'schemes') return <SchemesView data={data} />;
        if (activeTab === 'archives') {
            return (
                <ArchivesView
                    archives={archives}
                    loading={loading}
                    selectedFY={selectedFY}
                    selectedPeriodKey={selectedPeriodKey}
                    onRefresh={loadData}
                    onPeriodChange={periodKey => loadData(selectedFY, periodKey)}
                    periods={data?.periods || []}
                    selectedPeriodShort={data?.selectedPeriodShort || data?.selectedPeriod}
                />
            );
        }
        return null;
    }, [activeTab, data, archives, loading, selectedFY, selectedPeriodKey]);

    return (
        <div className={`app-layout ${isDarkMode ? 'dark-theme' : 'light-theme'}`}>
            <nav className="sidebar">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        className={`sidebar-item ${activeTab === tab.id ? 'active' : ''}`}
                        onClick={() => setActiveTab(tab.id)}
                    >
                        {tab.label}
                    </button>
                ))}
            </nav>
            <main className="main-content">
                <header className="app-header">
                    <div>
                        <h1>Weekly AMFI Dashboard</h1>
                        <p>Kotak weekly AUM, gross sales, net sales, category movement, and scheme drill-down.</p>
                    </div>
                </header>
                {error ? (
                    <div className="error-banner">
                        <span>Error: {error}</span>
                        <button onClick={() => setError('')} title="Dismiss error">&times;</button>
                    </div>
                ) : null}
                <div className="page-content">{content}</div>
            </main>
            <button
                className="theme-toggle"
                onClick={() => setIsDarkMode(value => !value)}
                title={isDarkMode ? 'Switch to light mode' : 'Switch to dark mode'}
                aria-label={isDarkMode ? 'Switch to light mode' : 'Switch to dark mode'}
            >
                {isDarkMode ? <Sun size={18} /> : <Moon size={18} />}
            </button>
        </div>
    );
}
