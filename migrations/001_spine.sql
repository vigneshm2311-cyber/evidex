-- Evidex spine schema (Layer 0).
-- Concepts are joined by assertions; each assertion is one guideline statement
-- with its own value, unit, population, grade, status and source anchor.

create table if not exists concept_type (
    name text primary key
);
insert into concept_type (name) values
    ('Condition'), ('Test'), ('Threshold'), ('Population'), ('RiskFactor'),
    ('Intervention'), ('DrugClass'), ('Complication'), ('Outcome'), ('Rule')
on conflict do nothing;

-- Fixed predicate list. Code validates against this too (evidex/spine/vocab.py).
create table if not exists predicate (
    name text primary key,
    requires_value boolean not null default false,
    description text not null
);
insert into predicate (name, requires_value, description) values
    ('classified_as',          false, 'subject is a category of object'),
    ('subtype_of',             false, 'subject is a subtype of object'),
    ('diagnosed_by',           false, 'condition is diagnosed by test (optionally at a threshold)'),
    ('has_threshold',          true,  'subject has a numeric threshold'),
    ('requires_confirmation',  false, 'diagnosis requires confirmation by rule'),
    ('unreliable_when',        false, 'test is unreliable in the object condition'),
    ('use_instead',            false, 'use object test instead of subject test'),
    ('screen_with',            false, 'condition is screened for with test, in population'),
    ('screening_starts_at',    true,  'screening starts at this age'),
    ('repeat_interval',        true,  'testing repeats at this interval'),
    ('risk_factor_for',        false, 'subject is a risk factor for object condition'),
    ('defined_by',             false, 'subject (often a population or stage) is defined by object'),
    -- Added 2026-11-06 for ADA Section 3 (prevention); see DECISIONS.md.
    ('prevented_or_delayed_by', false, 'onset of condition may be prevented or delayed by intervention'),
    ('has_target',             true,  'intervention has a numeric target for an outcome'),
    ('requires_monitoring_of', false, 'intervention requires periodic monitoring of test')
on conflict (name) do update
    set requires_value = excluded.requires_value, description = excluded.description;

create table if not exists concept (
    id        text primary key check (id ~ '^[a-z]+:[a-z0-9_]+$'),
    type      text not null references concept_type(name),
    name      text not null,
    synonyms  text[] not null default '{}',
    codes     jsonb not null default '{}'::jsonb,
    notes     text
);

create table if not exists source_anchor (
    id       text primary key,
    doc      text not null,
    edition  text not null,
    section  text not null,
    locator  text,          -- Rec 2.x / Table 2.x; TODO(doctor) until entered
    page     text,
    url      text
);

create table if not exists assertion (
    id                text primary key,
    subject_id        text not null references concept(id),
    predicate         text not null references predicate(name),
    object_id         text references concept(id),
    operator          text check (operator in ('>=', '>', '<=', '<', '=', 'range')),
    value             numeric,
    value_high        numeric,
    unit              text,
    population_id     text references concept(id),
    condition_text    text,
    grade             text,  -- from the document; TODO(doctor) until entered
    status            text not null default 'draft'
                      check (status in ('draft', 'proposed', 'accepted', 'rejected', 'superseded')),
    source_anchor_id  text not null references source_anchor(id),
    added_by          text not null,
    reviewed_by       text,
    doctor_checked    boolean not null default false,  -- checked against the 2026 text?
    notes             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    -- value shape: a value needs an operator and unit; a range needs both ends, low < high
    check ((value is null and value_high is null and operator is null)
           or (value is not null and operator is not null and unit is not null)),
    check ((operator = 'range') = (value_high is not null)),
    check (operator <> 'range' or value < value_high)
);

create index if not exists assertion_subject_idx on assertion (subject_id, predicate);
create index if not exists assertion_object_idx on assertion (object_id);
create index if not exists assertion_population_idx on assertion (population_id);

create table if not exists change_log (
    id         bigserial primary key,
    record_table text not null,
    record_id  text not null,
    action     text not null,  -- insert / update / delete, or e.g. 'proposed->accepted'
    actor      text not null,
    at         timestamptz not null default now(),
    before     jsonb,
    after      jsonb
);

-- Database-side checks the FK cannot express.
create or replace function assertion_guard() returns trigger language plpgsql
set search_path = public as $$
declare
    pop_type text;
    needs_value boolean;
begin
    if new.population_id is not null then
        select type into pop_type from concept where id = new.population_id;
        if pop_type <> 'Population' then
            raise exception 'assertion %: population_id % is a %, not a Population',
                new.id, new.population_id, pop_type;
        end if;
    end if;
    select requires_value into needs_value from predicate where name = new.predicate;
    if needs_value and new.value is null then
        raise exception 'assertion %: predicate % requires a value', new.id, new.predicate;
    end if;
    new.updated_at := now();
    return new;
end $$;

drop trigger if exists assertion_guard_trg on assertion;
create trigger assertion_guard_trg before insert or update on assertion
    for each row execute function assertion_guard();

-- Every change to an assertion is logged. The actor comes from the session
-- setting evidex.actor (set by the importer / app), falling back to the DB user.
create or replace function assertion_log() returns trigger language plpgsql
set search_path = public as $$
declare
    who text := coalesce(nullif(current_setting('evidex.actor', true), ''), current_user);
    act text;
begin
    if tg_op = 'INSERT' then
        insert into change_log (record_table, record_id, action, actor, after)
        values ('assertion', new.id, 'insert:' || new.status, who, to_jsonb(new));
        return new;
    elsif tg_op = 'UPDATE' then
        if (to_jsonb(old) - 'updated_at') = (to_jsonb(new) - 'updated_at') then
            return new;  -- no real change
        end if;
        act := case when old.status <> new.status
                    then old.status || '->' || new.status else 'update' end;
        insert into change_log (record_table, record_id, action, actor, before, after)
        values ('assertion', new.id, act, who, to_jsonb(old), to_jsonb(new));
        return new;
    else
        insert into change_log (record_table, record_id, action, actor, before)
        values ('assertion', old.id, 'delete', who, to_jsonb(old));
        return old;
    end if;
end $$;

drop trigger if exists assertion_log_trg on assertion;
create trigger assertion_log_trg after insert or update or delete on assertion
    for each row execute function assertion_log();

-- Supabase exposes the public schema over its REST API. Turn on row-level security
-- with no policies so the anon/authenticated keys can read nothing; the app connects
-- as the database owner, which bypasses RLS.
alter table concept_type  enable row level security;
alter table predicate     enable row level security;
alter table concept       enable row level security;
alter table source_anchor enable row level security;
alter table assertion     enable row level security;
alter table change_log    enable row level security;
