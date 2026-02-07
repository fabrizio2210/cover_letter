package models

import (
	"github.com/fabrizio2210/cover_letter/src/go/internal/proto/common"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

// Field represents a field of study or industry.
type Field struct {
	ID    primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Field string             `bson:"field" json:"field"`
}

// Company represents a company.
type Company struct {
	ID        primitive.ObjectID `bson:"_id,omitempty" json:"id,omitempty"`
	Name      string             `bson:"name" json:"name"`
	FieldID   primitive.ObjectID `bson:"field,omitempty" json:"fieldId,omitempty"`
	FieldInfo *Field             `bson:"fieldInfo,omitempty" json:"fieldInfo,omitempty"`
}

// Recipient represents a person or company to whom a cover letter is addressed.
type Recipient struct {
	ID          primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Email       string             `bson:"email" json:"email"`
	Name        string             `bson:"name,omitempty" json:"name,omitempty"`
	Description string             `bson:"description,omitempty" json:"description,omitempty"`
	CompanyID   primitive.ObjectID `bson:"company,omitempty" json:"companyId,omitempty"`
	CompanyInfo []Company          `bson:"companyInfo,omitempty" json:"companyInfo,omitempty"`
}

// Identity represents a user's identity.
type Identity struct {
	ID            primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Identity      string             `bson:"identity" json:"identity"`
	Name          string             `bson:"name,omitempty" json:"name,omitempty"`
	Description   string             `bson:"description,omitempty" json:"description,omitempty"`
	FieldID       primitive.ObjectID `bson:"field,omitempty" json:"-"`
	HtmlSignature string             `bson:"html_signature,omitempty" json:"html_signature,omitempty"`
	FieldInfo     []Field            `bson:"fieldInfo,omitempty" json:"fieldInfo,omitempty"`
}

// CoverLetter represents a cover letter.

type CoverLetter = common.CoverLetter
