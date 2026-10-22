-- Content can be refreshed without changing a course ID/version.
ALTER TABLE course_bundles ADD COLUMN content_digest TEXT NOT NULL DEFAULT '';
