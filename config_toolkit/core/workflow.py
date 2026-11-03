"""Generic explicit table previews, human approvals and recoverable writes."""
import base64
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import uuid
from .common import ToolError, atomic_json, canonical, file_hash, file_lock, load_json, _unique
from .excel import get_sheet, read_workbook, workbook_path, column_name
from .ooxml import append_records, patch_cells, stage_many


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{32}",value):
        raise ToolError("identity", "Use an ID returned by this tool.")
    return value


def _same(before, actual):
    return type(before) is type(actual) and before == actual


def _cell_value(value):
    if isinstance(value,(list,dict)):
        return canonical(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ToolError("type", "Non-finite numbers are unsupported.")
        return int(value) if value.is_integer() else value
    if value is not None and not isinstance(value,(str,int,float,bool)):
        raise ToolError("type", "Cell values must be JSON scalar values.")
    if isinstance(value,str) and (len(value)>32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]",value)):
        raise ToolError("cell_value", "Cell text exceeds spreadsheet limits or contains invalid XML control characters.")
    return "" if value is None else value


def _typed(value, kind, field):
    if value in (None, ""):
        return
    kind=str(kind or "string").lower()
    valid=True
    if kind in ("int","long","integer"):
        valid=type(value) is int
    elif kind in ("float","double","number","decimal"):
        valid=type(value) in (int,float) and math.isfinite(value)
    elif kind in ("bool","boolean"):
        valid=type(value) is bool
    elif kind in ("string","str","text"):
        valid=isinstance(value,str)
    elif kind in ("json","array","object") or kind.endswith("[]"):
        try:
            parsed=json.loads(value,object_pairs_hook=_unique,
                              parse_constant=lambda token: (_ for _ in ()).throw(ToolError("json_number",token))) if isinstance(value,str) else value
            if kind == "object":
                valid=isinstance(parsed,dict)
            elif kind == "array" or kind.endswith("[]"):
                valid=isinstance(parsed,list)
            else:
                valid=isinstance(parsed,(dict,list,int,float,str,bool)) or parsed is None
            if kind.endswith("[]") and valid:
                for item in parsed:
                    _typed(item,kind[:-2],field)
        except (ValueError,TypeError):
            valid=False
    else:
        raise ToolError("unsupported_type", "Register an explicit adapter for type " + kind + " at " + field)
    if not valid:
        raise ToolError("type", field + " does not match declared type " + kind)


class _Repository:
    def __init__(self,project,root_name):
        self.project,self.root_name=project,root_name
        self.books={}
        self.hashes={}

    def load(self,filename):
        if filename not in self.books:
            path=workbook_path(self.project,self.root_name,filename)
            before=file_hash(path)
            self.books[filename]=read_workbook(self.project,self.root_name,filename)
            if file_hash(path)!=before or any(sheet.source_sha256!=before for sheet in self.books[filename]):
                raise ToolError("source_changed", "Workbook changed around snapshot acquisition: " + filename)
            self.hashes[filename]=before
        return self.books[filename]

    def sheet(self,filename,title):
        matches=[sheet for sheet in self.load(filename) if sheet.title==title]
        if len(matches)!=1:
            raise ToolError("sheet", "Select one actual worksheet.")
        return matches[0]

    def table(self,name):
        aliases=self.project.config.get("aliases",{})
        name=aliases.get(name,name)
        selected=self.project.config.get("tables",{}).get(name)
        if selected is not None:
            if isinstance(selected,dict):
                selected=[selected]
            selected=[item for item in selected if item.get("root",self.root_name)==self.root_name]
            if not selected:
                raise ToolError("reference_source", "No explicit source for this table in the selected root.")
            result=[self.sheet(item["file"],item["sheet"]) for item in selected]
            if any(sheet.table!=name for sheet in result):
                raise ToolError("logical_table", "Configured table source has a different logical identity.")
            return result
        index_paths=(self.project.config.get("knowledge") or {}).get("index",[])
        if isinstance(index_paths,str):
            index_paths=[index_paths]
        indexed=[]
        found_index=False
        for configured in index_paths:
            path=self.project.resolve_config_path(configured)
            if not path.is_file():
                continue
            index=load_json(path)
            if index.get("kind")!="table_index" or index.get("project_id")!=self.project.project_id:
                raise ToolError("index_scope", "Reference index belongs to another project or format.")
            root_value=index.get("roots",{}).get(self.root_name)
            if root_value is None:
                continue
            if Path(root_value).resolve()!=self.project.root(self.root_name):
                raise ToolError("index_scope", "Reference index root differs from the project profile.")
            found_index=True
            indexed.extend(row for row in index.get("tables",[]) if row.get("root")==self.root_name and row.get("table")==name)
        if found_index:
            unique={(row["file"],row["sheet"]) for row in indexed}
            result=[self.sheet(file,title) for file,title in sorted(unique)]
            if not result or any(sheet.table!=name for sheet in result):
                raise ToolError("reference_source", "Refresh the schema index for reference table " + name)
            return result
        result=[]
        root=self.project.root(self.root_name)
        for path in sorted(root.rglob("*.xlsx")):
            if path.name.startswith("~$"):
                continue
            result.extend(sheet for sheet in self.load(path.relative_to(root).as_posix()) if sheet.table==name)
        if not result:
            raise ToolError("reference_source", "No declared source for reference table " + name)
        return result


class Workflow:
    def __init__(self,project,root_name):
        self.project,self.root_name=project,root_name
        self.root=project.root(root_name)
        key=hashlib.sha256((project.project_id+"\0"+str(self.root)).encode()).hexdigest()[:20]
        self.directory=project.state_dir/"workflow"/key
        self.directory.mkdir(parents=True,exist_ok=True)
        secret_path=self.directory/"secret.key"
        with file_lock(self.directory/"secret.lock"):
            if not secret_path.exists():
                handle=os.open(secret_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(handle,"wb") as stream:
                    stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
            self.secret=secret_path.read_bytes()
        if len(self.secret)!=32:
            raise ToolError("state", "Invalid local signing key; do not reuse another toolkit's state.")
        self.lock=self.directory/"operation.lock"

    def _sign(self,payload):
        payload=copy.deepcopy(payload)
        payload.pop("signature",None)
        payload["signature"]=hmac.new(self.secret,canonical(payload).encode(),hashlib.sha256).hexdigest()
        return payload

    def _save(self,folder,identifier,payload):
        atomic_json(self.directory/folder/(_id(identifier)+".json"),self._sign(payload))

    def _load(self,folder,identifier):
        path=self.directory/folder/(_id(identifier)+".json")
        if not path.is_file():
            raise ToolError("state_missing", "Unknown " + folder + " identity.")
        payload=load_json(path)
        signature=payload.get("signature","")
        if not isinstance(signature,str) or not hmac.compare_digest(self._sign(payload)["signature"],signature):
            raise ToolError("state_integrity", "Stored state failed its integrity check.")
        if payload.get("project_id")!=self.project.project_id or payload.get("root")!=str(self.root):
            raise ToolError("state_scope", "Stored state belongs to a different project/root.")
        return payload

    def _inventory(self):
        return sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*.xlsx") if not path.name.startswith("~$"))

    def _source_check(self,preview,skip_target=False):
        if file_hash(self.project.path)!=preview["project_sha256"]:
            raise ToolError("project_changed", "Project profile changed; create a new preview.")
        if self._inventory()!=preview["root_inventory"]:
            raise ToolError("source_changed", "Workbook source inventory changed; create a new preview.")
        for filename,expected in preview["source_hashes"].items():
            if skip_target and filename==preview["target"]["file"]:
                continue
            path=workbook_path(self.project,self.root_name,filename)
            if not path.is_file() or file_hash(path)!=expected:
                raise ToolError("source_changed", "Source or dependency changed: " + filename)

    def _references(self,repository,sheet,records):
        rules={}
        for field,constraint in sheet.constraints.items():
            match=re.search(r"FOREIGN:([^\s.]+)\.([^\s;]+)",constraint)
            if match:
                rules[field]={"target_table":match[1],"target_field":match[2],"nullable":True}
        for rule in self.project.config.get("references",[]):
            if rule.get("table")==sheet.table:
                if rule.get("field") not in sheet.fields:
                    raise ToolError("reference_contract", "Reference declaration uses a missing field.")
                rules[rule["field"]]=rule
        for field,rule in rules.items():
            targets=repository.table(rule["target_table"])
            keys={}
            for target in targets:
                if rule["target_field"] not in target.fields:
                    raise ToolError("reference_contract", "Referenced field is absent.")
                rows=records if target.file==sheet.file and target.title==sheet.title else target.records
                for row in rows:
                    value=row["values"].get(rule["target_field"])
                    keys[str(value)]=keys.get(str(value),0)+1
            for row in records:
                value=row["values"][field]
                if value in (None,""):
                    if not rule.get("nullable",True):
                        raise ToolError("reference", "Empty required reference: " + field)
                    continue
                values=json.loads(value) if str(sheet.types.get(field,"")).endswith("[]") else [value]
                for key in values:
                    if keys.get(str(key),0)!=1:
                        raise ToolError("reference", "Reference is missing or ambiguous: " + field + " -> " + str(key))

    def preview(self,request):
        if not isinstance(request,dict) or request.get("operation") not in ("patch","append"):
            raise ToolError("request", "Declare operation=patch|append, file, sheet and rows.")
        if request.get("root",self.root_name)!=self.root_name:
            raise ToolError("scope", "Request root differs from selected root.")
        filename,title=request.get("file"),request.get("sheet")
        if not isinstance(filename,str) or not isinstance(title,str):
            raise ToolError("request", "Explicit file and sheet are required.")
        rows=request.get("rows")
        if not isinstance(rows,list) or not rows:
            raise ToolError("request", "At least one explicit row is required.")
        with file_lock(self.lock):
            repository=_Repository(self.project,self.root_name)
            inventory=self._inventory()
            sheet=repository.sheet(filename,title)
            before=copy.deepcopy(sheet.records)
            after=copy.deepcopy(before)
            changes=[]
            if request["operation"]=="patch":
                by_id={str(row["id"]):row for row in after}
                seen=set()
                for row in rows:
                    key=str(row.get("id"))
                    if key in seen or key not in by_id:
                        raise ToolError("row", "Missing or repeated target record: " + key)
                    seen.add(key)
                    selected=by_id[key]
                    edits=row.get("changes")
                    if isinstance(edits,dict):
                        edits=[dict(change,field=field) for field,change in edits.items()]
                    if not isinstance(edits,list) or not edits:
                        raise ToolError("changes", "Each target row requires explicit field/before/after changes.")
                    field_seen=set()
                    for edit in edits:
                        field=edit.get("field")
                        if field not in sheet.fields or field in field_seen or field==sheet.primary:
                            raise ToolError("field", "Unknown, duplicate or PRIMARY field edit: " + str(field))
                        field_seen.add(field)
                        if "before" not in edit or "after" not in edit or not _same(edit["before"],selected["values"][field]):
                            raise ToolError("before_mismatch", "Exact before value does not match: " + field)
                        value=_cell_value(edit["after"])
                        _typed(value,sheet.types.get(field),field)
                        address=column_name(sheet.fields[field])+str(selected["row"])
                        selected["values"][field]=value
                        changes.append({"id":selected["id"],"field":field,"address":address,"before":edit["before"],"after":value})
                xml=patch_cells(sheet,changes)
            else:
                existing={str(row["id"]) for row in after}
                values=[]
                for offset,row in enumerate(rows,1):
                    if not isinstance(row,dict) or set(row)!=set(sheet.fields):
                        raise ToolError("append_fields", "Append rows must provide every exported field exactly once.")
                    row={field:_cell_value(value) for field,value in row.items()}
                    key=row[sheet.primary]
                    if key in (None,"") or str(key) in existing:
                        raise ToolError("primary", "Missing or duplicate new PRIMARY: " + str(key))
                    existing.add(str(key))
                    for field,value in row.items():
                        _typed(value,sheet.types.get(field),field)
                    values.append(row)
                    after.append({"row":sheet.end+offset,"id":key,"values":row})
                    changes.append({"id":key,"before":None,"after":row})
                xml=append_records(sheet,values,self.project.layout)
            self._references(repository,sheet,after)
            identifier=uuid.uuid4().hex
            preview={"schema_version":1,"kind":"table_preview","project_id":self.project.project_id,"root_name":self.root_name,
                     "root":str(self.root),"project_sha256":self.project.digest,"preview_id":identifier,"created_at":_now(),
                     "status":"pending","operation":request["operation"],"target":{"file":filename,"sheet":title,"table":sheet.table},
                     "schema":sheet.schema(),"changes":changes,"before":before,"after":after,"source_hashes":repository.hashes,
                     "root_inventory":inventory,"worksheet_member":sheet.member,"worksheet_xml":base64.b64encode(xml).decode("ascii")}
            preview["preview_token"]=hmac.new(self.secret,canonical(preview).encode(),hashlib.sha256).hexdigest()
            self._source_check(preview)
            self._save("previews",identifier,preview)
            return self._public(preview)

    @staticmethod
    def _public(preview):
        return copy.deepcopy({key:value for key,value in preview.items() if key not in ("worksheet_xml","signature")})

    def get_preview(self,preview_id):
        return self._public(self._load("previews",preview_id))

    def list_previews(self):
        directory=self.directory/"previews"
        return [self.get_preview(path.stem) for path in sorted(directory.glob("*.json"))] if directory.exists() else []

    def approve(self,preview_id,actor,reason):
        if not isinstance(actor,str) or not actor.strip() or not isinstance(reason,str) or not reason.strip():
            raise ToolError("approval", "A human reviewer name and explicit confirmation reason are required.")
        with file_lock(self.lock):
            preview=self._load("previews",preview_id)
            if preview["status"]!="pending":
                raise ToolError("approval", "Only pending previews can be approved.")
            self._source_check(preview)
            identifier=uuid.uuid4().hex
            approval={"schema_version":1,"kind":"human_approval","project_id":self.project.project_id,"root":str(self.root),
                      "approval_id":identifier,"preview_id":preview_id,"preview_token":preview["preview_token"],
                      "actor":actor.strip(),"reason":reason.strip(),"created_at":_now(),"status":"approved"}
            self._save("approvals",identifier,approval)
            return {key:value for key,value in approval.items() if key!="signature"}

    def _validate_saved(self,preview):
        sheet=get_sheet(self.project,self.root_name,preview["target"]["file"],preview["target"]["sheet"])
        if sheet.table!=preview["target"]["table"] or sheet.records!=preview["after"]:
            raise ToolError("readback", "Saved records differ from the approved preview.")
        return {"status":"passed","records":len(sheet.records),"source_sha256":sheet.source_sha256}

    def apply(self,preview_id,approval_id):
        _id(approval_id)
        with file_lock(self.lock):
            preview=self._load("previews",preview_id)
            approval=self._load("approvals",approval_id)
            if approval.get("preview_id")!=preview_id or approval.get("preview_token")!=preview["preview_token"]:
                raise ToolError("approval", "Approval is for another exact preview.")
            if preview["status"]=="completed":
                return dict(preview["receipt"],idempotent=True)
            # A process may stop after the durable completion journal and
            # before the secondary preview/approval records are updated.
            for path in (self.directory/"transactions").glob("*.json"):
                committed=self._load("transactions",path.stem)
                if (committed.get("status")=="completed" and committed.get("preview_id")==preview_id
                        and committed.get("approval_id")==approval_id):
                    preview.update(status="completed",receipt=committed["receipt"])
                    self._save("previews",preview_id,preview)
                    approval["status"]="consumed"
                    self._save("approvals",approval_id,approval)
                    return dict(committed["receipt"],idempotent=True)
            if approval["status"]!="approved":
                raise ToolError("approval", "Approval is no longer available.")
            for path in (self.directory/"transactions").glob("*.json"):
                prior=self._load("transactions",path.stem)
                if prior["status"] not in ("completed","rolled_back"):
                    raise ToolError("recovery_required", "Recover pending transaction before writing: " + path.stem)
            self._source_check(preview)
            target=workbook_path(self.project,self.root_name,preview["target"]["file"])
            if (target.parent/("~$"+target.name)).exists():
                raise ToolError("excel_open", "Close the workbook in Excel before writing.")
            transaction_id=uuid.uuid4().hex
            transaction_dir=self.directory/"transaction-files"/transaction_id
            transaction_dir.mkdir(parents=True)
            backup=transaction_dir/"before.xlsx"
            staged=transaction_dir/"after.xlsx"
            shutil.copyfile(target,backup)
            if file_hash(backup)!=preview["source_hashes"][preview["target"]["file"]]:
                raise ToolError("source_changed", "Source changed during backup.")
            xml=base64.b64decode(preview["worksheet_xml"],validate=True)
            stage_many(backup,staged,{preview["worksheet_member"]:xml})
            with staged.open("r+b") as stream:
                os.fsync(stream.fileno())
            after_hash=file_hash(staged)
            journal={"schema_version":1,"kind":"workbook_transaction","project_id":self.project.project_id,"root":str(self.root),
                     "root_name":self.root_name,"transaction_id":transaction_id,"preview_id":preview_id,"approval_id":approval_id,
                     "file":preview["target"]["file"],"before_sha256":file_hash(backup),"after_sha256":after_hash,
                     "status":"prepared","created_at":_now()}
            self._save("transactions",transaction_id,journal)
            replacement=None
            try:
                self._source_check(preview)
                handle,replacement=tempfile.mkstemp(prefix=".config-write-",suffix=".tmp",dir=target.parent)
                with os.fdopen(handle,"wb") as stream:
                    stream.write(staged.read_bytes());stream.flush();os.fsync(stream.fileno())
                if file_hash(replacement)!=after_hash:
                    raise ToolError("stage", "Staged image checksum mismatch.")
                journal["status"]="applying"
                self._save("transactions",transaction_id,journal)
                self._source_check(preview)
                os.replace(replacement,target)
                checks=self._validate_saved(preview)
                self._source_check(preview,skip_target=True)
                if file_hash(target)!=after_hash:
                    raise ToolError("source_changed", "Target changed during readback.")
                receipt={"status":"completed","project_id":self.project.project_id,"preview_id":preview_id,"approval_id":approval_id,
                         "transaction_id":transaction_id,"target":preview["target"],"before_sha256":journal["before_sha256"],
                         "after_sha256":after_hash,"backup":str(backup),"readback":checks,"completed_at":_now(),"business_writes":1}
                journal.update(status="completed",receipt=receipt)
                self._save("transactions",transaction_id,journal)
                preview.update(status="completed",receipt=receipt)
                self._save("previews",preview_id,preview)
                approval["status"]="consumed"
                self._save("approvals",approval_id,approval)
                return receipt
            except BaseException as error:
                journal["failure"]={"type":type(error).__name__,"message":str(error)}
                if journal.get("status")!="completed":
                    self._save("transactions",transaction_id,journal)
                recovered=self._recover_locked(transaction_id)
                if recovered["status"]=="recovery_required":
                    raise ToolError("recovery_required", "External edits prevent safe rollback; transaction: " + transaction_id)
                raise
            finally:
                if replacement and os.path.exists(replacement):
                    os.unlink(replacement)

    def _recover_locked(self,transaction_id):
        journal=self._load("transactions",transaction_id)
        if journal["status"] in ("completed","rolled_back"):
            return {"status":journal["status"],"transaction_id":transaction_id,"business_writes":0}
        target=workbook_path(self.project,self.root_name,journal["file"])
        backup=self.directory/"transaction-files"/transaction_id/"before.xlsx"
        current=file_hash(target) if target.is_file() else None
        if current==journal["before_sha256"]:
            journal["status"]="rolled_back"
        elif current==journal["after_sha256"] and backup.is_file() and file_hash(backup)==journal["before_sha256"]:
            handle,restore=tempfile.mkstemp(prefix=".config-restore-",suffix=".tmp",dir=target.parent)
            try:
                with os.fdopen(handle,"wb") as stream:
                    stream.write(backup.read_bytes());stream.flush();os.fsync(stream.fileno())
                if file_hash(target)!=journal["after_sha256"] or file_hash(restore)!=journal["before_sha256"]:
                    journal["status"]="recovery_required"
                else:
                    os.replace(restore,target)
                    journal["status"]="rolled_back" if file_hash(target)==journal["before_sha256"] else "recovery_required"
            finally:
                if os.path.exists(restore):
                    os.unlink(restore)
        else:
            journal["status"]="recovery_required"
        self._save("transactions",transaction_id,journal)
        return {"status":journal["status"],"transaction_id":transaction_id,"business_writes":0}

    def recover(self,transaction_id):
        with file_lock(self.lock):
            return self._recover_locked(_id(transaction_id))
