ALTER TABLE teaching_course_units ADD COLUMN IF NOT EXISTS work_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE teaching_course_submissions ADD COLUMN IF NOT EXISTS unit_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS teaching_unit_submissions ON teaching_course_submissions(run_key,unit_id,status);
CREATE TABLE IF NOT EXISTS teaching_course_epochs (
    scope TEXT NOT NULL, chapter_id TEXT NOT NULL, epoch TEXT NOT NULL,
    PRIMARY KEY(scope,chapter_id)
);
