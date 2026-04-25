package facade

import (
	authdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/auth"
	companiesdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/companies"
	coverlettersdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/coverletters"
	crawlsdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/crawls"
	fieldsdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/fields"
	identitiesdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/identities"
	jobsdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/jobs"
	recipientsdomain "github.com/fabrizio2210/cover_letter/src/go/cmd/api/domains/recipients"
)

// Login is implemented in the auth domain slice.
var Login = authdomain.Login

// Compatibility exports to keep route wiring centralized while migration is in progress.
var GetRecipients = recipientsdomain.GetRecipients
var CreateRecipient = recipientsdomain.CreateRecipient
var DeleteRecipient = recipientsdomain.DeleteRecipient
var UpdateRecipientDescription = recipientsdomain.UpdateRecipientDescription
var UpdateRecipientName = recipientsdomain.UpdateRecipientName
var AssociateCompanyWithRecipient = recipientsdomain.AssociateCompanyWithRecipient
var GenerateCoverLetterForRecipient = recipientsdomain.GenerateCoverLetterForRecipient

var GetIdentities = identitiesdomain.GetIdentities
var CreateIdentity = identitiesdomain.CreateIdentity
var DeleteIdentity = identitiesdomain.DeleteIdentity
var UpdateIdentityDescription = identitiesdomain.UpdateIdentityDescription
var UpdateIdentityName = identitiesdomain.UpdateIdentityName
var UpdateIdentitySignature = identitiesdomain.UpdateIdentitySignature
var UpdateIdentityRoles = identitiesdomain.UpdateIdentityRoles
var UpdateIdentityPreferences = identitiesdomain.UpdateIdentityPreferences
var AssociateFieldWithIdentity = identitiesdomain.AssociateFieldWithIdentity

var GetFields = fieldsdomain.GetFields
var CreateField = fieldsdomain.CreateField
var UpdateField = fieldsdomain.UpdateField
var DeleteField = fieldsdomain.DeleteField

var GetCompanies = companiesdomain.GetCompanies
var CreateCompany = companiesdomain.CreateCompany
var UpdateCompany = companiesdomain.UpdateCompany
var AssociateFieldWithCompany = companiesdomain.AssociateFieldWithCompany
var DeleteCompany = companiesdomain.DeleteCompany

var GetCoverLetters = coverlettersdomain.GetCoverLetters
var GetCoverLetter = coverlettersdomain.GetCoverLetter
var DeleteCoverLetter = coverlettersdomain.DeleteCoverLetter
var UpdateCoverLetter = coverlettersdomain.UpdateCoverLetter
var RefineCoverLetter = coverlettersdomain.RefineCoverLetter
var SendCoverLetter = coverlettersdomain.SendCoverLetter

var GetJobDescriptions = jobsdomain.GetJobDescriptions
var StreamJobUpdates = jobsdomain.StreamJobUpdates
var GetJobDescription = jobsdomain.GetJobDescription
var GetJobPreferenceScores = jobsdomain.GetJobPreferenceScores
var CreateJobDescription = jobsdomain.CreateJobDescription
var UpdateJobDescription = jobsdomain.UpdateJobDescription
var DeleteJobDescription = jobsdomain.DeleteJobDescription
var ScoreJobDescription = jobsdomain.ScoreJobDescription
var CheckJobDescription = jobsdomain.CheckJobDescription

var TriggerCrawl = crawlsdomain.TriggerCrawl
var GetActiveCrawls = crawlsdomain.GetActiveCrawls
var GetLastRunWorkflowStats = crawlsdomain.GetLastRunWorkflowStats
var GetWorkflowCumulativeJobs = crawlsdomain.GetWorkflowCumulativeJobs
var GetActivitySummary = crawlsdomain.GetActivitySummary
var StreamCrawlProgress = crawlsdomain.StreamCrawlProgress
var GetActiveScoring = crawlsdomain.GetActiveScoring
var StreamScoringProgress = crawlsdomain.StreamScoringProgress
