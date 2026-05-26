import fs from "fs";
import path from "path";
import { createReadStream, statSync } from "fs";

const BLOB_TOKEN = "vercel_blob_rw_VLKgBkoVeGNwSGjf_NvUNx4uoxzrymRBvq4jgqFJtDy7dfB";
const DATA_DIR = "./backend/data";

const CONTENT_TYPES = {
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".json": "application/json",
};

async function uploadFile(localPath, blobPath) {
  const ext = path.extname(localPath).toLowerCase();
  const contentType = CONTENT_TYPES[ext] ?? "application/octet-stream";
  const fileBuffer = fs.readFileSync(localPath);
  const size = statSync(localPath).size;

  process.stdout.write(`  Uploading ${path.basename(localPath)} (${(size/1024).toFixed(0)}KB)... `);

  const res = await fetch(`https://blob.vercel-storage.com/${blobPath}`, {
    method: "PUT",
    headers: {
      "Authorization": `Bearer ${BLOB_TOKEN}`,
      "x-content-type": contentType,
    },
    body: fileBuffer,
  });

  if (!res.ok) {
    const text = await res.text();
    console.log(`FAILED: ${text}`);
    return;
  }

  const data = await res.json();
  console.log("OK");
  return data.url;
}

// Upload ENR xlsx files — walk subfolders recursively
function walkXlsx(dir, base = "") {
  const entries = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const relPath = base ? `${base}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      entries.push(...walkXlsx(path.join(dir, entry.name), relPath));
    } else if (entry.name.endsWith(".xlsx")) {
      entries.push({ localPath: path.join(dir, entry.name), relPath });
    }
  }
  return entries;
}

const enrDir = path.join(DATA_DIR, "enr");
const enrFiles = walkXlsx(enrDir).sort((a, b) => a.relPath.localeCompare(b.relPath));
console.log(`\nUploading ${enrFiles.length} ENR files...`);
for (const { localPath, relPath } of enrFiles) {
  await uploadFile(localPath, `enr-data/${relPath}`);
}

// Upload static files
console.log("\nUploading static files...");
for (const f of ["cci.xlsx", "fmi_forecast.json"]) {
  const localPath = path.join(DATA_DIR, f);
  if (fs.existsSync(localPath)) {
    await uploadFile(localPath, `static-data/${f}`);
  } else {
    console.log(`  SKIP: ${f} not found`);
  }
}

// Upload research files
const researchDir = path.join(DATA_DIR, "research");
if (fs.existsSync(researchDir)) {
  const researchFiles = fs.readdirSync(researchDir).filter(f => f.endsWith(".json"));
  if (researchFiles.length) {
    console.log("\nUploading research files...");
    for (const f of researchFiles) {
      await uploadFile(path.join(researchDir, f), `research-data/${f}`);
    }
  }
}

console.log("\nAll uploads complete.");
