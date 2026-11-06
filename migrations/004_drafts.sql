-- Drafts produced by the pipeline and the doctor's review of each claim.
create table if not exists draft (
    id                text primary key,           -- the pipeline run_id
    request           text not null,
    artifact          text not null,
    audience          text not null,
    title             text not null,
    status            text not null default 'in_review' check (status in ('in_review', 'signed_off')),
    model_not_covered jsonb not null default '[]'::jsonb,
    created_at        timestamptz not null default now(),
    signed_by         text,
    signed_at         timestamptz
);

create table if not exists claim (
    id               bigserial primary key,
    draft_id         text not null references draft(id) on delete cascade,
    section          text not null,
    section_pos      int not null,
    pos              int not null,
    original_text    text not null,             -- as the Synthesizer wrote it
    text             text not null,             -- current text (after any doctor edit)
    assertion_ids    text[] not null,
    verdict          jsonb not null,            -- latest Verifier result
    status           text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    numbers_checked  boolean not null default false,
    reviewed_by      text,
    reviewed_at      timestamptz,
    updated_at       timestamptz not null default now()
);
create index if not exists claim_draft_idx on claim (draft_id, section_pos, pos);

-- Signed-off drafts are locked.
create or replace function claim_guard() returns trigger language plpgsql
set search_path = public as $$
declare st text;
begin
    select status into st from draft where id = coalesce(new.draft_id, old.draft_id);
    if st = 'signed_off' and tg_op <> 'INSERT' then
        raise exception 'draft % is signed off and locked', coalesce(new.draft_id, old.draft_id);
    end if;
    if tg_op = 'UPDATE' then new.updated_at := now(); end if;
    return coalesce(new, old);
end $$;
drop trigger if exists claim_guard_trg on claim;
create trigger claim_guard_trg before update or delete on claim
    for each row execute function claim_guard();

-- Doctor decisions and edits on claims go to the same change log as the spine.
create or replace function claim_log() returns trigger language plpgsql
set search_path = public as $$
declare who text := coalesce(nullif(current_setting('evidex.actor', true), ''), current_user);
begin
    if (to_jsonb(old) - 'updated_at') = (to_jsonb(new) - 'updated_at') then return new; end if;
    insert into change_log (record_table, record_id, action, actor, before, after)
    values ('claim', new.id::text,
            case when old.status <> new.status then old.status || '->' || new.status
                 when old.text <> new.text then 'edit' else 'update' end,
            who, to_jsonb(old), to_jsonb(new));
    return new;
end $$;
drop trigger if exists claim_log_trg on claim;
create trigger claim_log_trg after update on claim
    for each row execute function claim_log();

create or replace function draft_log() returns trigger language plpgsql
set search_path = public as $$
declare who text := coalesce(nullif(current_setting('evidex.actor', true), ''), current_user);
begin
    if old.status <> new.status then
        insert into change_log (record_table, record_id, action, actor, before, after)
        values ('draft', new.id, old.status || '->' || new.status, who, to_jsonb(old), to_jsonb(new));
    end if;
    return new;
end $$;
drop trigger if exists draft_log_trg on draft;
create trigger draft_log_trg after update on draft
    for each row execute function draft_log();

alter table draft enable row level security;
alter table claim enable row level security;
