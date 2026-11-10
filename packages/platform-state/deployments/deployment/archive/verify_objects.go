package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

type checkpoint struct {
	SessionID      string `json:"session_id"`
	ContentHash    string `json:"content_hash"`
	CheckpointHash string `json:"checkpoint_hash"`
	ObjectKey      string `json:"object_key"`
	ManifestKey    string `json:"manifest_key"`
	EventCount     int    `json:"event_count"`
	Complete       bool   `json:"complete"`
}

func main() {
	if err := verify(); err != nil {
		fmt.Fprintln(os.Stderr, "Archive object verification failed:", err)
		os.Exit(1)
	}
}

func verify() error {
	if len(os.Args) != 3 || !regexp.MustCompile(`^http://(10\.152\.183\.[0-9]{1,3}:9000|127\.0\.0\.1:[0-9]{1,5})$`).MatchString(os.Args[1]) {
		return fmt.Errorf("expected existing private RustFS ClusterIP origin and canary index JSON file")
	}
	state := "/home/nibeauli/kagent-lab/factory-coding/archive"
	access, err := os.ReadFile(filepath.Join(state, "access-key"))
	if err != nil {
		return fmt.Errorf("protected object credentials unavailable")
	}
	secret, err := os.ReadFile(filepath.Join(state, "secret-key"))
	if err != nil {
		return fmt.Errorf("protected object credentials unavailable")
	}
	raw, err := os.ReadFile(os.Args[2])
	if err != nil {
		return err
	}
	var rows []checkpoint
	if err := json.Unmarshal(raw, &rows); err != nil || len(rows) == 0 {
		return fmt.Errorf("nonempty canary index required")
	}
	client := s3.NewFromConfig(aws.Config{Region: "us-east-1",
		Credentials: aws.NewCredentialsCache(credentials.NewStaticCredentialsProvider(
			strings.TrimSpace(string(access)), strings.TrimSpace(string(secret)), "")),
		HTTPClient: &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{Proxy: nil}}},
		func(options *s3.Options) {
			options.BaseEndpoint = aws.String(os.Args[1])
			options.UsePathStyle = true
			options.RetryMaxAttempts = 1
		})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	get := func(key string) ([]byte, error) {
		output, err := client.GetObject(ctx, &s3.GetObjectInput{
			Bucket: aws.String("bespoke-session-archives"), Key: &key})
		if err != nil {
			return nil, fmt.Errorf("private archive object unavailable")
		}
		defer output.Body.Close()
		value, err := io.ReadAll(io.LimitReader(output.Body, 5*1024*1024+1))
		if err != nil || len(value) > 5*1024*1024 {
			return nil, fmt.Errorf("object size/read failure")
		}
		return value, nil
	}
	report := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		path := regexp.MustCompile(`^v1/[a-f0-9]{64}/` + regexp.QuoteMeta(row.SessionID) + `/[a-f0-9]{64}\.`)
		if !path.MatchString(row.ObjectKey) || !path.MatchString(row.ManifestKey) {
			return fmt.Errorf("indexed object scope invalid")
		}
		compressed, err := get(row.ObjectKey)
		if err != nil {
			return err
		}
		reader, err := gzip.NewReader(bytes.NewReader(compressed))
		if err != nil {
			return fmt.Errorf("invalid JSONL compression")
		}
		events, err := io.ReadAll(io.LimitReader(reader, 4*1024*1024+1))
		reader.Close()
		sum := sha256.Sum256(events)
		if err != nil || len(events) > 4*1024*1024 || hex.EncodeToString(sum[:]) != row.ContentHash {
			return fmt.Errorf("indexed event checksum mismatch")
		}
		count := 0
		keys := map[string]bool{}
		type parentRecord struct {
			Key    string `json:"sourceEventKey"`
			Parent string `json:"parentEventKey"`
			Type   string `json:"eventType"`
			Event  struct {
				Parent    string `json:"parentId"`
				Ephemeral bool   `json:"ephemeral"`
			} `json:"event"`
		}
		var records []parentRecord
		for _, line := range bytes.Split(events, []byte("\n")) {
			if len(bytes.TrimSpace(line)) == 0 {
				continue
			}
			if !json.Valid(line) {
				return fmt.Errorf("invalid logical event")
			}
			var record parentRecord
			if json.Unmarshal(line, &record) != nil {
				return fmt.Errorf("invalid event metadata")
			}
			keys[record.Key] = true
			records = append(records, record)
			count++
		}
		manifestBytes, err := get(row.ManifestKey)
		if err != nil {
			return err
		}
		manifestSum := sha256.Sum256(manifestBytes)
		if hex.EncodeToString(manifestSum[:]) != row.CheckpointHash {
			return fmt.Errorf("canonical manifest checksum mismatch")
		}
		var manifest struct {
			SessionID      string `json:"sessionId"`
			Hash           string `json:"sha256"`
			EventCount     int    `json:"eventCount"`
			Complete       bool   `json:"complete"`
			ObjectKey      string `json:"objectKey"`
			SourceManifest struct {
				Completeness string           `json:"completeness"`
				Gaps         []map[string]any `json:"gaps"`
				Counts       map[string]int   `json:"counts"`
			} `json:"sourceManifest"`
		}
		if json.Unmarshal(manifestBytes, &manifest) != nil || manifest.SessionID != row.SessionID ||
			manifest.Hash != row.ContentHash || manifest.EventCount != count || count != row.EventCount ||
			manifest.Complete != row.Complete || manifest.ObjectKey != row.ObjectKey {
			return fmt.Errorf("canonical manifest/index mismatch")
		}
		var parents []map[string]any
		for _, record := range records {
			if record.Parent != "" && !keys[record.Parent] {
				parents = append(parents, map[string]any{"eventType": record.Type,
					"emptyParent": record.Event.Parent == "", "ephemeral": record.Event.Ephemeral})
			}
		}
		report = append(report, map[string]any{"sessionId": row.SessionID, "sha256": row.ContentHash,
			"eventCount": count, "complete": row.Complete, "objectVerified": true, "manifestVerified": true,
			"completeness": manifest.SourceManifest.Completeness, "gaps": manifest.SourceManifest.Gaps,
			"eventTypes": manifest.SourceManifest.Counts, "unresolvedParentMetadata": parents})
	}
	return json.NewEncoder(os.Stdout).Encode(report)
}
