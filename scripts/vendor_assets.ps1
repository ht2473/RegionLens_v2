# =====================================================================================
# Загрузка сторонних клиентских библиотек и шрифтов в репозиторий.
#
# Внешние CDN не используются: работа в закрытой сети, строгая политика
# Content-Security-Policy, неизменные версии.
#
# Запуск:  pwsh scripts/vendor_assets.ps1
# =====================================================================================

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$staticDir = Join-Path $root 'static'

# Каталоги назначения.
$dirs = @(
    (Join-Path $staticDir 'vendor'),
    (Join-Path $staticDir 'fonts')
)
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

# Перечень ассетов: назначение, источник, минимальный ожидаемый размер в байтах.
$assets = @(
    @{
        Path    = 'vendor/htmx.min.js'
        Url     = 'https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js'
        MinSize = 30000
        Note    = 'HTMX 2.0.10 — обмен фрагментами разметки с сервером'
    },
    @{
        Path    = 'vendor/sortable.min.js'
        Url     = 'https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js'
        MinSize = 40000
        Note    = 'SortableJS 1.15.6 — перетаскивание карточек исследования, в том числе пальцем'
    },
    @{
        Path    = 'fonts/onest-cyrillic.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource-variable/onest@5.2.5/files/onest-cyrillic-wght-normal.woff2'
        MinSize = 10000
        Note    = 'Onest, переменный вес 300-800, кириллический поднабор — весь интерфейс'
    },
    @{
        Path    = 'fonts/onest-latin.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource-variable/onest@5.2.5/files/onest-latin-wght-normal.woff2'
        MinSize = 20000
        Note    = 'Onest, переменный вес 300-800, латинский поднабор'
    },
    @{
        Path    = 'fonts/roboto-condensed-400.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/roboto-condensed@5.1.1/files/roboto-condensed-cyrillic-400-normal.woff2'
        MinSize = 5000
        Note    = 'Roboto Condensed Regular, кириллический поднабор — плотные места'
    },
    @{
        Path    = 'fonts/roboto-condensed-latin-400.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/roboto-condensed@5.1.1/files/roboto-condensed-latin-400-normal.woff2'
        MinSize = 5000
        Note    = 'Roboto Condensed Regular, латинский поднабор'
    },
    @{
        Path    = 'fonts/roboto-condensed-600.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/roboto-condensed@5.1.1/files/roboto-condensed-cyrillic-600-normal.woff2'
        MinSize = 5000
        Note    = 'Roboto Condensed SemiBold, кириллический поднабор — заголовки таблиц'
    },
    @{
        Path    = 'fonts/roboto-condensed-latin-600.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/roboto-condensed@5.1.1/files/roboto-condensed-latin-600-normal.woff2'
        MinSize = 5000
        Note    = 'Roboto Condensed SemiBold, латинский поднабор'
    },
    @{
        Path    = 'fonts/jetbrains-mono-400.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.1.0/files/jetbrains-mono-cyrillic-400-normal.woff2'
        MinSize = 5000
        Note    = 'JetBrains Mono — числовые значения и коды'
    }
    @{
        Path    = 'fonts/jetbrains-mono-latin-400.woff2'
        Url     = 'https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.1.1/files/jetbrains-mono-latin-400-normal.woff2'
        MinSize = 10000
        Note    = 'JetBrains Mono Regular, латинский поднабор — код и ключи рядов'
    },
)

Write-Host 'Загрузка клиентских ассетов' -ForegroundColor Cyan

foreach ($asset in $assets) {
    $target = Join-Path $staticDir $asset.Path

    if (Test-Path $target) {
        $existing = (Get-Item $target).Length
        if ($existing -ge $asset.MinSize) {
            Write-Host ("  = {0} ({1:N0} Б) — уже загружен" -f $asset.Path, $existing)
            continue
        }
    }

    Invoke-WebRequest -Uri $asset.Url -OutFile $target -UseBasicParsing
    $size = (Get-Item $target).Length

    if ($size -lt $asset.MinSize) {
        Remove-Item $target -Force
        throw ("Файл {0} загружен не полностью: {1} Б, ожидалось не менее {2} Б" -f `
                $asset.Path, $size, $asset.MinSize)
    }

    Write-Host ("  + {0} ({1:N0} Б) — {2}" -f $asset.Path, $size, $asset.Note) -ForegroundColor Green
}

# ECharts не загружается: `static/vendor/echarts.min.js` собран из нужных частей
# (scripts/echarts/README.md).
$echarts = Join-Path $staticDir 'vendor/echarts.min.js'
if (Test-Path $echarts) {
    Write-Host ("  = vendor/echarts.min.js ({0:N0} Б) — сборка из частей, не загружается" -f `
            (Get-Item $echarts).Length)
} else {
    Write-Warning 'Нет vendor/echarts.min.js: соберите по scripts/echarts/README.md'
}

Write-Host 'Готово' -ForegroundColor Cyan
