package common

import timestamppb "google.golang.org/protobuf/types/known/timestamppb"

// JobRetireNotification is published to job_retire_notification_channel by the
// enrichment_retiring_jobs worker when a job has been closed (is_open=false) or
// permanently deleted.
//
// NOTE: manually maintained; mirrors the JobRetireNotification message in
// common.proto. Update both when the schema changes.
type JobRetireNotification struct {
	JobId     string                 `json:"job_id"`
	IsOpen    bool                   `json:"is_open"`
	Deleted   bool                   `json:"deleted"`
	EmittedAt *timestamppb.Timestamp `json:"emitted_at,omitempty"`
}
