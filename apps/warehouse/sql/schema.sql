-- =====================================================================================
-- Схема аналитического склада (DuckDB): «звезда» с фактами fact_observation, версии
-- значений по выпускам fact_vintage и витрины, рассчитываемые при сборке.
-- Выполняется командой `etl_build`; все объекты пересоздаются.
--
-- Ключи таблиц — логические: PRIMARY KEY не объявлен, потому что индексы ограничений
-- заняли бы больше половины файла, а выборок не ускоряют. Единственность ключей
-- проверяет сборка (`pipeline.check_unique_keys`).
-- =====================================================================================

DROP TABLE IF EXISTS mart_source_link;
DROP TABLE IF EXISTS mart_revision;
DROP TABLE IF EXISTS mart_rank;
DROP TABLE IF EXISTS mart_series_stats;
DROP TABLE IF EXISTS mart_series_coverage;
DROP TABLE IF EXISTS fact_month;
DROP TABLE IF EXISTS fact_vintage;
DROP TABLE IF EXISTS fact_observation;
DROP TABLE IF EXISTS dim_series;
DROP TABLE IF EXISTS dim_indicator;
DROP TABLE IF EXISTS dim_section;
DROP TABLE IF EXISTS dim_unit;
DROP TABLE IF EXISTS dim_edition;
DROP TABLE IF EXISTS dim_territory;
DROP TABLE IF EXISTS meta_build;

-- -------------------------------------------------------------------------------------
-- Измерения
-- -------------------------------------------------------------------------------------

-- Территории — из справочника PostgreSQL: в источнике нет пригодного ключа территории.
CREATE TABLE dim_territory (
    territory_code   VARCHAR NOT NULL,        -- ISO 3166-2 для субъектов, FD-* для округов, RU для страны
    source_name      VARCHAR NOT NULL,        -- точное название в исходных данных
    name_ru          VARCHAR NOT NULL,
    name_en          VARCHAR,
    abbreviation     VARCHAR,
    level            VARCHAR NOT NULL,        -- country | federal_district | region
    territory_type   VARCHAR,
    district_code    VARCHAR,                 -- федеральный округ субъекта
    is_aggregate     BOOLEAN NOT NULL DEFAULT FALSE,
    okato            VARCHAR,
    area_km2         DOUBLE,
    utc_offset       SMALLINT,
    data_since_year  SMALLINT,
    display_order    SMALLINT
);

-- Тематические разделы сборников.
CREATE TABLE dim_section (
    section_code     VARCHAR NOT NULL,        -- адресный идентификатор раздела
    source_name      VARCHAR NOT NULL,
    name_ru          VARCHAR NOT NULL,
    name_en          VARCHAR,
    indicator_count  INTEGER  NOT NULL DEFAULT 0,
    series_count     INTEGER  NOT NULL DEFAULT 0
);

-- Единицы измерения с категорией и коэффициентом приведения значения по России.
CREATE TABLE dim_unit (
    unit_code            VARCHAR NOT NULL,
    source_name          VARCHAR NOT NULL,
    name_ru              VARCHAR NOT NULL,
    name_en              VARCHAR,
    short_name_ru        VARCHAR,
    short_name_en        VARCHAR,
    kind                 VARCHAR NOT NULL,    -- absolute | currency | share | index | rate | physical | unknown
    multiplier           DOUBLE  NOT NULL DEFAULT 1,
    derived_from_name    BOOLEAN NOT NULL DEFAULT FALSE,
    -- «Миллионов рублей; для значений в целом по России: млрд руб»: коэффициент
    -- приводит значение по России к единице территорий.
    country_scale        DOUBLE  NOT NULL DEFAULT 1,
    country_scale_unknown BOOLEAN NOT NULL DEFAULT FALSE,
    observation_count    INTEGER NOT NULL DEFAULT 0
);

-- Выпуски статистических изданий: сборники набора и выпуски внешних источников.
CREATE TABLE dim_edition (
    edition_code      VARCHAR NOT NULL,
    source_name       VARCHAR NOT NULL,
    publication_ru    VARCHAR NOT NULL,       -- издание без года
    publication_en    VARCHAR,
    edition_year      SMALLINT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    first_year        SMALLINT,
    last_year         SMALLINT,
    edition_label     VARCHAR NOT NULL,       -- «2025» у сборника, «07-2026» у выпуска источника
    released_on       DATE,                   -- дата выхода выпуска источника; у сборников неизвестна
    source_code       VARCHAR,                -- модуль источника; NULL — сборник набора
    -- Порядок выхода: больший номер — более поздний выпуск, его значение побеждает.
    edition_rank      SMALLINT
);

-- Показатели.
CREATE TABLE dim_indicator (
    indicator_code    VARCHAR NOT NULL,
    name_ru           VARCHAR NOT NULL,
    name_en           VARCHAR,
    section_code      VARCHAR NOT NULL,
    series_count      SMALLINT NOT NULL DEFAULT 0,
    observation_count INTEGER  NOT NULL DEFAULT 0,
    first_year        SMALLINT,
    last_year         SMALLINT
);

-- Ряды: пара «показатель + разрез».
CREATE TABLE dim_series (
    series_key         VARCHAR NOT NULL,
    indicator_code     VARCHAR NOT NULL,
    section_code       VARCHAR NOT NULL,
    unit_code          VARCHAR,
    indicator_name_ru  VARCHAR NOT NULL,
    indicator_name_en  VARCHAR,
    subsection_ru      VARCHAR,               -- NULL, если разрез отсутствует
    subsection_en      VARCHAR,
    has_subsection     BOOLEAN NOT NULL DEFAULT FALSE,
    full_title_ru      VARCHAR NOT NULL,      -- «показатель — разрез» для заголовков графиков
    -- Из справочника переводов; NULL — перевода нет.
    full_title_en      VARCHAR,
    polarity           VARCHAR NOT NULL DEFAULT 'unknown',
    -- Модуль внешнего источника ряда проекта (Банк России, ФНС); NULL — ряд набора.
    source_code        VARCHAR
);

-- -------------------------------------------------------------------------------------
-- Факты
-- -------------------------------------------------------------------------------------

-- Каноническое наблюдение на «ряд + территория + год»: побеждает более поздний выпуск.
CREATE TABLE fact_observation (
    series_key      VARCHAR  NOT NULL,
    indicator_code  VARCHAR  NOT NULL,
    section_code    VARCHAR  NOT NULL,
    territory_code  VARCHAR  NOT NULL,
    territory_level VARCHAR  NOT NULL,
    district_code   VARCHAR,
    is_aggregate    BOOLEAN  NOT NULL DEFAULT FALSE,
    year            SMALLINT NOT NULL,
    value           DOUBLE,                   -- NULL для любого типа пропуска
    quality         TINYINT  NOT NULL,        -- 0 наблюдение, 1 нет данных, 2 скрыто, 3 неприменимо
    edition_code    VARCHAR,
    edition_year    SMALLINT,
    revision_count  TINYINT  NOT NULL DEFAULT 0,   -- сколько раз значение пересматривалось
    -- Признаки значения: 1 — предварительное, 2 — рассчитано, 4 — знаменатель заморожен.
    flags           TINYINT  NOT NULL DEFAULT 0
);

-- Все версии значения по выпускам.
CREATE TABLE fact_vintage (
    series_key      VARCHAR  NOT NULL,
    territory_code  VARCHAR  NOT NULL,
    year            SMALLINT NOT NULL,
    edition_code    VARCHAR  NOT NULL,
    edition_year    SMALLINT NOT NULL,
    value           DOUBLE,
    quality         TINYINT  NOT NULL,
    flags           TINYINT  NOT NULL DEFAULT 0     -- признаки значения (ValueFlag)
);

-- Помесячный слой: значения по месяцам из выпусков источников; из выпусков побеждает поздний.
CREATE TABLE fact_month (
    series_key      VARCHAR  NOT NULL,        -- годовой ряд, к которому относится месячное значение
    territory_code  VARCHAR  NOT NULL,
    year            SMALLINT NOT NULL,
    month           TINYINT  NOT NULL,        -- месяц, которым заканчивается период
    -- level — значение месяца или на его конец; ytd — с начала года (сумма или индекс
    -- к декабрю); yoy — к тому же периоду прошлого года, как публикует источник.
    kind            VARCHAR  NOT NULL,
    value           DOUBLE,
    quality         TINYINT  NOT NULL,
    flags           TINYINT  NOT NULL DEFAULT 0,
    edition_code    VARCHAR  NOT NULL
);

-- -------------------------------------------------------------------------------------
-- Витрины
-- -------------------------------------------------------------------------------------

-- Покрытие и качество каждого ряда: по ним ряд предлагается инструментам анализа.
CREATE TABLE mart_series_coverage (
    series_key             VARCHAR NOT NULL,
    observation_count      INTEGER  NOT NULL DEFAULT 0,
    value_count            INTEGER  NOT NULL DEFAULT 0,
    no_data_count          INTEGER  NOT NULL DEFAULT 0,
    hidden_count           INTEGER  NOT NULL DEFAULT 0,
    region_count           SMALLINT NOT NULL DEFAULT 0,
    year_count             SMALLINT NOT NULL DEFAULT 0,
    first_year             SMALLINT,
    last_year              SMALLINT,
    region_coverage        DOUBLE   NOT NULL DEFAULT 0,   -- доля субъектов с данными
    completeness           DOUBLE   NOT NULL DEFAULT 0,   -- доля заполненных ячеек «регион × год»
    longest_gap_free_span  SMALLINT NOT NULL DEFAULT 0,
    is_analysis_ready      BOOLEAN  NOT NULL DEFAULT FALSE
);

-- Статистики распределения по субъектам за каждый год.
CREATE TABLE mart_series_stats (
    series_key     VARCHAR  NOT NULL,
    year           SMALLINT NOT NULL,
    observations   SMALLINT NOT NULL,
    mean_value     DOUBLE,
    median_value   DOUBLE,
    min_value      DOUBLE,
    max_value      DOUBLE,
    p10_value      DOUBLE,
    p25_value      DOUBLE,
    p75_value      DOUBLE,
    p90_value      DOUBLE,
    stddev_value   DOUBLE,
    sum_value      DOUBLE,
    coef_variation DOUBLE,                    -- σ / среднее: базовая мера дифференциации
    decile_ratio   DOUBLE,                    -- отношение девятого дециля к первому
    range_ratio    DOUBLE,                    -- отношение максимума к минимуму
    country_value  DOUBLE                     -- значение по Российской Федерации в целом
);

-- Ранги субъектов внутри ряда и года.
CREATE TABLE mart_rank (
    series_key      VARCHAR  NOT NULL,
    year            SMALLINT NOT NULL,
    territory_code  VARCHAR  NOT NULL,
    value           DOUBLE   NOT NULL,
    rank_desc       SMALLINT NOT NULL,        -- 1 — наибольшее значение
    rank_asc        SMALLINT NOT NULL,        -- 1 — наименьшее значение
    percentile      DOUBLE   NOT NULL,
    quintile        TINYINT  NOT NULL,        -- 1..5, группа по величине значения
    share_of_total  DOUBLE,                   -- доля территории в сумме по субъектам
    ratio_to_country DOUBLE                   -- отношение к значению по стране
);

-- Пересмотры: наблюдения, значение которых различается между выпусками изданий.
CREATE TABLE mart_revision (
    series_key      VARCHAR  NOT NULL,
    territory_code  VARCHAR  NOT NULL,
    year            SMALLINT NOT NULL,
    edition_count   TINYINT  NOT NULL,
    first_value     DOUBLE,                   -- значение из самого раннего выпуска
    last_value      DOUBLE,                   -- значение из самого позднего выпуска
    abs_change      DOUBLE,
    rel_change      DOUBLE                    -- относительное изменение, доля
);

-- Связи рядов с внешними источниками: способ, стык и итог сверки с набором.
CREATE TABLE mart_source_link (
    series_key        VARCHAR  NOT NULL,
    source_code       VARCHAR  NOT NULL,
    method            VARCHAR  NOT NULL,
    mode              VARCHAR  NOT NULL,      -- continue — продолжение, supersede — новая редакция
    link              VARCHAR  NOT NULL,      -- full | conditional | revision
    dataset_last_year SMALLINT,               -- последний год ряда в наборе
    junction_year     SMALLINT,               -- первый год значений источника после набора
    last_year         SMALLINT,               -- последний год значений источника
    release_code      VARCHAR,                -- выпуск, по которому сверено
    compared_from     SMALLINT,
    compared_to       SMALLINT,
    pairs             INTEGER  NOT NULL DEFAULT 0,
    within_share      DOUBLE,                 -- доля пар «субъект × год» в допуске
    median_deviation  DOUBLE,
    max_deviation     DOUBLE,
    deviation_unit    VARCHAR,                -- share | points
    tolerance         DOUBLE,
    worst_territory   VARCHAR,
    worst_year        SMALLINT,
    revised_count     INTEGER  NOT NULL DEFAULT 0,   -- значений набора, уточнённых источником
    loaded_count      INTEGER  NOT NULL DEFAULT 0    -- версий значений из выпусков источника
);

-- Сноски последнего выпуска источника к рядам: по ним ставятся разрывы сопоставимости.
CREATE TABLE mart_source_note (
    series_key   VARCHAR  NOT NULL,
    source_code  VARCHAR  NOT NULL,
    edition_code VARCHAR  NOT NULL,
    position     SMALLINT NOT NULL,               -- порядок сноски в выпуске
    note_text    VARCHAR  NOT NULL
);

-- Сведения о сборке склада: версия данных, время, контрольные суммы.
CREATE TABLE meta_build (
    key        VARCHAR NOT NULL,
    value      VARCHAR
);
