package main

import (
	"context"
	"errors"
	"testing"

	"github.com/kagent-dev/kagent/go/core/pkg/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

type archiveTestSession struct{ binding *auth.RuntimeBinding }

func (session archiveTestSession) Principal() auth.Principal {
	return auth.Principal{RuntimeBinding: session.binding}
}

type archiveTestStore struct {
	records []archiveRecord
	fail    bool
}

func (store *archiveTestStore) Put(_ context.Context, record archiveRecord, _ []byte) error {
	if store.fail {
		return errors.New("private database diagnostic")
	}
	store.records = append(store.records, record)
	return nil
}

func archiveInput(session, events string) *structpb.Struct {
	value, _ := structpb.NewStruct(map[string]any{"sessionId": session, "taskId": "task-1",
		"manifest": map[string]any{"complete": true}, "events": events})
	return value
}

func TestArchiveUsesVerifiedActorBindingAndRejectsCrossSession(t *testing.T) {
	identifier := "01a1a366-25f8-7bb2-8ea1-0dd685d34882"
	store := &archiveTestStore{}
	service := &archiveService{store}
	input := archiveInput(identifier, "{\"id\":\"event-1\",\"type\":\"assistant.message\"}\n")
	if _, err := service.PutBatch(context.Background(), input); status.Code(err) != codes.Unauthenticated {
		t.Fatal("anonymous archive submission accepted")
	}
	ctx := auth.AuthSessionTo(context.Background(), archiveTestSession{&auth.RuntimeBinding{SessionID: identifier, CreatorID: "owner"}})
	other := archiveInput("01a1a366-25f8-7bb2-8ea1-0dd685d34883", input.Fields["events"].GetStringValue())
	if _, err := service.PutBatch(ctx, other); status.Code(err) != codes.PermissionDenied {
		t.Fatal("cross-session archive accepted")
	}
	output, err := service.PutBatch(ctx, input)
	if err != nil || output.Fields["status"].GetStringValue() != "exported" || len(store.records) != 1 {
		t.Fatalf("bound session archive failed: %v", err)
	}
	if store.records[0].CreatorHash == "owner" || store.records[0].EventCount != 1 {
		t.Fatal("archive scope or event count incorrect")
	}
}

func TestArchiveRejectsUnredactedCredentialsAndReportsPendingOnOutage(t *testing.T) {
	identifier := "01a1a366-25f8-7bb2-8ea1-0dd685d34882"
	ctx := auth.AuthSessionTo(context.Background(), archiveTestSession{&auth.RuntimeBinding{SessionID: identifier, CreatorID: "owner"}})
	store := &archiveTestStore{}
	service := &archiveService{store}
	if _, err := service.PutBatch(ctx, archiveInput(identifier, "{\"text\":\"Bearer abcdefghijklmnopqrstuvwxyz123456\"}\n")); status.Code(err) != codes.InvalidArgument {
		t.Fatal("unredacted credential content accepted")
	}

	if len(store.records) != 0 {
		t.Fatal("rejected archive reached storage")
	}
	store.fail = true
	if _, err := service.PutBatch(ctx, archiveInput(identifier, "{\"id\":\"event-1\"}\n")); status.Code(err) != codes.Unavailable {
		t.Fatal("storage failure did not leave export pending")
	}
}

func TestArchiveRetainsChangedCompletenessForIdenticalEventBytes(t *testing.T) {
	identifier := "01a1a366-25f8-7bb2-8ea1-0dd685d34882"
	ctx := auth.AuthSessionTo(context.Background(), archiveTestSession{&auth.RuntimeBinding{SessionID: identifier, CreatorID: "owner"}})
	store := &archiveTestStore{}
	service := &archiveService{store}
	input := archiveInput(identifier, "{\"id\":\"event-1\"}\n")
	manifest := input.Fields["manifest"].GetStructValue()
	manifest.Fields["completeness"] = structpb.NewStringValue("complete")
	if _, err := service.PutBatch(ctx, input); err != nil {
		t.Fatal(err)
	}
	manifest.Fields["completeness"] = structpb.NewStringValue("partial")
	if _, err := service.PutBatch(ctx, input); err != nil {
		t.Fatal(err)
	}
	if !store.records[0].Complete || store.records[1].Complete ||
		store.records[0].Hash != store.records[1].Hash || store.records[0].CheckpointHash == store.records[1].CheckpointHash {
		t.Fatal("a later incomplete checkpoint lost its status or corrupted shared event content")
	}
}
