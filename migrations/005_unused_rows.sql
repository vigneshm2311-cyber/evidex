-- Spine rows the Planner chose for a section that no claim ended up citing (e.g. a condition
-- left out of a merged list). Shown to the reviewer as a warning; {heading: [assertion ids]}.
alter table draft add column if not exists unused_rows jsonb not null default '{}'::jsonb;
