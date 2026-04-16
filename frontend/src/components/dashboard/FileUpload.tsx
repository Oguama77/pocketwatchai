import { Upload, FileText, Loader2, CheckCircle2 } from "lucide-react";
import { useState, useCallback } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { uploadFinancialDocument } from "@/lib/api";
import type { AnalyticsResponse } from "@/types/analytics";

interface FileUploadProps {
  onUploaded: (data: AnalyticsResponse) => void;
}

export function FileUpload({ onUploaded }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const processFile = useCallback(async (file: File) => {
    setError(null);
    setUploadedFileName(file.name);
    setIsProcessing(true);
    try {
      const response = await uploadFinancialDocument(file);
      localStorage.setItem("pocketwatch_session_id", response.sessionId);
      onUploaded(response);
    } catch (uploadErr) {
      setError(uploadErr instanceof Error ? uploadErr.message : "Upload failed.");
    } finally {
      setIsProcessing(false);
    }
  }, [onUploaded]);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFiles = Array.from(e.dataTransfer.files);
    setFiles((prev) => [...prev, ...droppedFiles]);
    if (!droppedFiles.length) return;
    await processFile(droppedFiles[0]);
  }, [processFile]);

  const handleFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.length) return;
    const selected = Array.from(e.target.files);
    setFiles((prev) => [...prev, ...selected]);
    const first = selected[0];
    await processFile(first);
  };

  return (
    <Card className="shadow-card animate-fade-in">
      <CardContent className="p-5">
        <div
          className={`border-2 border-dashed rounded-lg p-8 text-center transition-all duration-200 ${
            isDragging ? "border-primary bg-accent/50" : "border-border hover:border-primary/40"
          }`}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
        >
          <Upload className="h-8 w-8 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm font-medium mb-1">Drop your financial documents here</p>
          <p className="text-xs text-muted-foreground mb-3">PDF, CSV, or Excel files</p>
          <label>
            <Button size="sm" className="cursor-pointer" asChild disabled={isProcessing}>
              <span>{isProcessing ? "Processing..." : "Browse Files"}</span>
            </Button>
            <input type="file" className="hidden" accept=".pdf,.csv,.xlsx,.xls" multiple onChange={handleFileInput} />
          </label>
        </div>
        {uploadedFileName && (
          <div className="mt-3 rounded-md border bg-muted/40 px-3 py-2 text-sm">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-primary" />
              <span className="font-medium">Uploaded:</span>
              <span className="truncate">{uploadedFileName}</span>
            </div>
            {isProcessing && (
              <div className="mt-2 flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>Processing document and extracting insights...</span>
              </div>
            )}
          </div>
        )}
        {error && <p className="mt-3 text-sm text-destructive">{error}</p>}

        {files.length > 0 && (
          <div className="mt-4 space-y-2">
            {files.map((f, i) => (
              <div key={i} className="flex items-center gap-2 text-sm p-2 rounded-md bg-muted">
                <FileText className="h-4 w-4 text-primary" />
                <span className="truncate">{f.name}</span>
                <span className="text-xs text-muted-foreground ml-auto">{(f.size / 1024).toFixed(1)} KB</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
