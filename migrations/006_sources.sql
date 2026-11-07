-- Literature layer: PubMed records and uploaded files that claims may cite next to spine rows.
create table if not exists source_doc (
    id          text primary key,              -- pmid:<number> or up:<sha256 prefix>
    kind        text not null check (kind in ('pubmed', 'upload')),
    title       text not null,
    citation    text not null,                 -- reference line shown in exports
    url         text,
    body        text not null,                 -- abstract (PubMed) or extracted text (upload)
    fetched_at  timestamptz not null default now()
);
alter table source_doc enable row level security;

-- What the user confirmed in phase 1 (topic, key points, persona, format, files).
alter table draft add column if not exists understanding jsonb not null default '{}'::jsonb;
