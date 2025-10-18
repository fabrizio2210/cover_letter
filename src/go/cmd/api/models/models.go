package models

import "go.mongodb.org/mongo-driver/bson/primitive"

// Field represents a field of study or industry.
type Field struct {
	ID    primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Field string             `bson:"field" json:"field"`
}

// Recipient represents a person or company to whom a cover letter is addressed.
type Recipient struct {
	ID          primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Email       string             `bson:"email" json:"email"`
	Name        string             `bson:"name,omitempty" json:"name,omitempty"`
	Description string             `bson:"description,omitempty" json:"description,omitempty"`
	FieldID     primitive.ObjectID `bson:"field,omitempty" json:"-"` // Use `json:"-"` to hide from JSON output
	FieldInfo   []Field            `bson:"fieldInfo,omitempty" json:"fieldInfo,omitempty"`
}

// Identity represents a user's identity.
type Identity struct {
	ID          primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Identity    string             `bson:"identity" json:"identity"`
	Name        string             `bson:"name,omitempty" json:"name,omitempty"`
	Description string             `bson:"description,omitempty" json:"description,omitempty"`
	FieldID     primitive.ObjectID `bson:"field,omitempty" json:"-"`
	FieldInfo   []Field            `bson:"fieldInfo,omitempty" json:"fieldInfo,omitempty"`
}

// CoverLetter represents a cover letter.
type CoverLetter struct {
	ID             primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	RecipientID    primitive.ObjectID `bson:"recipient_id" json:"recipientId"`
	ConversationID string             `bson:"conversation_id" json:"conversationId"`
	CoverLetter    string             `bson:"cover_letter" json:"coverLetter"`
	CreatedAt      primitive.DateTime `bson:"created_at" json:"createdAt"`
	UpdatedAt      primitive.DateTime `bson:"updated_at" json:"updatedAt"`
	RecipientInfo  []Recipient        `bson:"recipientInfo,omitempty" json:"recipientInfo,omitempty"`
}
