package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/kagent-dev/kagent/go/core/pkg/auth"
	"github.com/kagent-dev/kagent/go/core/pkg/postgres"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

const archiveMethod = "/factory.archive.v1.ArchiveService/PutBatch"

type archiveBatch struct {
	SessionID string         `json:"sessionId"`
	TaskID    string         `json:"taskId"`
	Manifest  map[string]any `json:"manifest"`
	Events    string         `json:"events"`
}

type archiveRecord struct {
	SessionID, CreatorHash, TaskID, Hash, ObjectKey, CheckpointHash, ManifestKey string
	EventCount                                                                   int
	Complete                                                                     bool
	ManifestJSON                                                                 []byte
}

type archiveStore interface {
	Put(context.Context, archiveRecord, []byte) error
}

type archiveRPC interface {
	PutBatch(context.Context, *structpb.Struct) (*structpb.Struct, error)
}

type archiveService struct{ store archiveStore }

var archiveCredential = regexp.MustCompile(`(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|[?&](?:sig|access_token|api_key)=[A-Za-z0-9%._~+/-]{16,}`)

func validateArchiveEvents(events string) (int, error) {
	if len(events) == 0 || len(events) > 4*1024*1024 {
		return 0, errors.New("archive size invalid")
	}
	candidate := strings.ReplaceAll(events, "Bearer kagent-credential-injected", "inert-placeholder")
	if archiveCredential.MatchString(candidate) {
		return 0, errors.New("archive credential content rejected")
	}
	count := 0
	for _, line := range strings.Split(events, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var event map[string]any
		if len(line) > 1024*1024 || json.Unmarshal([]byte(line), &event) != nil || event == nil {
			return 0, errors.New("archive event invalid")
		}
		count++
	}
	if count == 0 {
		return 0, errors.New("archive has no events")
	}
	return count, nil
}

func (service *archiveService) PutBatch(ctx context.Context, input *structpb.Struct) (*structpb.Struct, error) {
	session, ok := auth.AuthSessionFrom(ctx)
	if !ok || session == nil {
		return nil, status.Error(codes.Unauthenticated, "verified actor required")
	}
	binding := session.Principal().RuntimeBinding
	if binding == nil || binding.SessionID == "" || binding.CreatorID == "" {
		return nil, status.Error(codes.PermissionDenied, "actor session binding required")
	}
	encoded, err := json.Marshal(input.AsMap())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid archive envelope")
	}
	var batch archiveBatch
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&batch); err != nil || batch.SessionID != binding.SessionID || len(batch.TaskID) > 256 {
		return nil, status.Error(codes.PermissionDenied, "archive session does not match actor")
	}
	if _, err := uuid.Parse(batch.SessionID); err != nil {
		return nil, status.Error(codes.InvalidArgument, "invalid session identity")
	}
	count, err := validateArchiveEvents(batch.Events)
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "archive data rejected")
	}
	owner := sha256.Sum256([]byte(binding.CreatorID))
	digest := sha256.Sum256([]byte(batch.Events))
	record := archiveRecord{SessionID: binding.SessionID, CreatorHash: hex.EncodeToString(owner[:]),
		TaskID: batch.TaskID, Hash: hex.EncodeToString(digest[:]), EventCount: count}
	if completeness, ok := batch.Manifest["completeness"].(string); ok {
		record.Complete = completeness == "complete"
	} else {
		record.Complete, _ = batch.Manifest["complete"].(bool)
	}
	if expected, ok := batch.Manifest["sha256"].(string); ok && expected != record.Hash {
		return nil, status.Error(codes.InvalidArgument, "archive checksum mismatch")
	}
	if expected, ok := batch.Manifest["eventCount"].(float64); ok && expected != float64(count) {
		return nil, status.Error(codes.InvalidArgument, "archive event count mismatch")
	}
	record.ObjectKey = "v1/" + record.CreatorHash + "/" + record.SessionID + "/" + record.Hash + ".jsonl.gz"
	sourceManifest := make(map[string]any, len(batch.Manifest))
	for key, value := range batch.Manifest {
		if key != "export" && key != "updatedAt" && key != "checkpointSha256" {
			sourceManifest[key] = value
		}
	}
	record.ManifestJSON, err = json.Marshal(map[string]any{"schemaVersion": 1, "sessionId": record.SessionID,
		"taskId": record.TaskID, "eventCount": count, "sha256": record.Hash, "objectKey": record.ObjectKey,
		"complete": record.Complete, "sourceManifest": sourceManifest})
	if err != nil || archiveCredential.Match(record.ManifestJSON) {
		return nil, status.Error(codes.InvalidArgument, "archive manifest rejected")
	}
	checkpoint := sha256.Sum256(record.ManifestJSON)
	record.CheckpointHash = hex.EncodeToString(checkpoint[:])
	record.ManifestKey = "v1/" + record.CreatorHash + "/" + record.SessionID + "/" + record.CheckpointHash + ".manifest.json"
	var compressed bytes.Buffer
	compressor := gzip.NewWriter(&compressed)
	if _, err := compressor.Write([]byte(batch.Events)); err != nil {
		return nil, status.Error(codes.Internal, "archive encoding failed")
	}
	if err := compressor.Close(); err != nil {
		return nil, status.Error(codes.Internal, "archive encoding failed")
	}
	if err := service.store.Put(ctx, record, compressed.Bytes()); err != nil {
		return nil, status.Error(codes.Unavailable, "archive persistence unavailable")
	}
	return structpb.NewStruct(map[string]any{"status": "exported", "sha256": record.Hash,
		"eventCount": record.EventCount, "complete": record.Complete, "objectKey": record.ObjectKey})
}

func registerArchiveService(registrar grpc.ServiceRegistrar, service archiveRPC) {
	registrar.RegisterService(&grpc.ServiceDesc{
		ServiceName: "factory.archive.v1.ArchiveService",
		HandlerType: (*archiveRPC)(nil),
		Methods: []grpc.MethodDesc{{MethodName: "PutBatch",
			Handler: func(server any, ctx context.Context, decode func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
				input := new(structpb.Struct)
				if err := decode(input); err != nil {
					return nil, err
				}
				call := func(ctx context.Context, request any) (any, error) {
					return server.(archiveRPC).PutBatch(ctx, request.(*structpb.Struct))
				}
				if interceptor == nil {
					return call(ctx, input)
				}
				return interceptor(ctx, input, &grpc.UnaryServerInfo{Server: server, FullMethod: archiveMethod}, call)
			}}},
	}, service)
}

type archiveStorageConfig struct {
	DatabaseURLFile string `json:"databaseUrlFile"`
	DatabaseRole    string `json:"databaseRole"`
	Endpoint        string `json:"endpoint"`
	Bucket          string `json:"bucket"`
	AccessKeyFile   string `json:"accessKeyFile"`
	SecretKeyFile   string `json:"secretKeyFile"`
}

type durableArchiveStore struct {
	database *pgxpool.Pool
	objects  *s3.Client
	bucket   string
}

func readArchiveSecret(path string) (string, error) {
	if path == "" {
		return "", errors.New("protected archive configuration required")
	}
	value, err := os.ReadFile(path)
	if err != nil || strings.TrimSpace(string(value)) == "" {
		return "", errors.New("protected archive configuration unavailable")
	}
	return strings.TrimSpace(string(value)), nil
}

func newDurableArchiveStore(ctx context.Context, file string) (*durableArchiveStore, error) {
	contents, err := os.ReadFile(file)
	if err != nil {
		return nil, errors.New("archive configuration unavailable")
	}
	var cfg archiveStorageConfig
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&cfg); err != nil || cfg.Bucket == "" {
		return nil, errors.New("invalid archive configuration")
	}
	origin, err := url.Parse(cfg.Endpoint)
	if err != nil || (origin.Scheme != "http" && origin.Scheme != "https") || origin.Hostname() == "" ||
		origin.User != nil || origin.RawQuery != "" || origin.Fragment != "" || (origin.Path != "" && origin.Path != "/") {
		return nil, errors.New("invalid archive object origin")
	}
	dsn, err := readArchiveSecret(cfg.DatabaseURLFile)
	if err != nil {
		return nil, err
	}
	access, err := readArchiveSecret(cfg.AccessKeyFile)
	if err != nil {
		return nil, err
	}
	secret, err := readArchiveSecret(cfg.SecretKeyFile)
	if err != nil {
		return nil, err
	}
	if cfg.DatabaseRole == "" {
		cfg.DatabaseRole = "factory_readwrite"
	}
	databaseConfig, err := postgres.PoolConfig(dsn, cfg.DatabaseRole)
	if err != nil {
		return nil, errors.New("invalid protected archive database configuration")
	}
	databaseConfig.MaxConns = 4
	databaseConfig.ConnConfig.ConnectTimeout = 10 * time.Second
	database, err := pgxpool.NewWithConfig(ctx, databaseConfig)
	if err != nil {
		return nil, errors.New("archive database unavailable")
	}
	objectConfig, err := awsconfig.LoadDefaultConfig(ctx, awsconfig.WithRegion("us-east-1"),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(access, secret, "")),
		awsconfig.WithHTTPClient(&http.Client{Timeout: 30 * time.Second}))
	if err != nil {
		database.Close()
		return nil, errors.New("archive object client unavailable")
	}
	objects := s3.NewFromConfig(objectConfig, func(options *s3.Options) {
		options.BaseEndpoint = aws.String(cfg.Endpoint)
		options.UsePathStyle = true
	})
	return &durableArchiveStore{database: database, objects: objects, bucket: cfg.Bucket}, nil
}

func (store *durableArchiveStore) Put(ctx context.Context, record archiveRecord, data []byte) error {
	if _, err := store.objects.PutObject(ctx, &s3.PutObjectInput{
		Bucket: &store.bucket, Key: &record.ObjectKey, Body: bytes.NewReader(data), ContentType: aws.String("application/gzip"),
		Metadata: map[string]string{"sha256": record.Hash, "schema": "bespoke-session-v1"},
	}); err != nil {
		return errors.New("archive object write failed")
	}
	if _, err := store.objects.PutObject(ctx, &s3.PutObjectInput{
		Bucket: &store.bucket, Key: &record.ManifestKey, Body: bytes.NewReader(record.ManifestJSON),
		ContentType: aws.String("application/json"),
	}); err != nil {
		return errors.New("archive manifest write failed")
	}
	result, err := store.database.Exec(ctx, `INSERT INTO factory.session_archives
		(session_id,creator_hash,task_id,content_hash,object_key,event_count,complete,checkpoint_hash,manifest_key)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
		ON CONFLICT(session_id,checkpoint_hash) DO NOTHING`,
		record.SessionID, record.CreatorHash, record.TaskID, record.Hash, record.ObjectKey, record.EventCount,
		record.Complete, record.CheckpointHash, record.ManifestKey)
	if err != nil {
		return errors.New("archive index write failed")
	}
	if result.RowsAffected() == 0 {
		var existing string
		if err := store.database.QueryRow(ctx, `SELECT creator_hash FROM factory.session_archives
			WHERE session_id=$1 AND checkpoint_hash=$2`, record.SessionID, record.CheckpointHash).Scan(&existing); err != nil || existing != record.CreatorHash {
			return errors.New("archive index scope conflict")
		}
	}
	return nil
}

func configureArchive(ctx context.Context) (func(grpc.ServiceRegistrar), map[string]auth.AccessMode, func(), error) {
	path := os.Getenv("ARCHIVE_CONFIG")
	if path == "" {
		return nil, nil, func() {}, nil
	}
	store, err := newDurableArchiveStore(ctx, path)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("archive capability unavailable: %w", err)
	}
	register := func(server grpc.ServiceRegistrar) { registerArchiveService(server, &archiveService{store}) }
	return register, map[string]auth.AccessMode{archiveMethod: auth.AccessRuntime}, store.database.Close, nil
}

func prepareArchiveStorage(ctx context.Context, file string) error {
	store, err := newDurableArchiveStore(ctx, file)
	if err != nil {
		return err
	}
	defer store.database.Close()
	if _, err := store.objects.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: &store.bucket}); err != nil {
		var failure smithy.APIError
		if !errors.As(err, &failure) || (failure.ErrorCode() != "NotFound" && failure.ErrorCode() != "NoSuchBucket") {
			return errors.New("archive bucket access unavailable")
		}
		if _, err := store.objects.CreateBucket(ctx, &s3.CreateBucketInput{Bucket: &store.bucket}); err != nil {
			return errors.New("archive bucket creation failed")
		}
	}
	return nil
}
