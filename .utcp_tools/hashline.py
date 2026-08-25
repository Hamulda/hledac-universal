import sys, hashlib, json

def hash_line(line: str) -> str:
    return hashlib.md5(line.rstrip('\r\n').encode('utf-8')).hexdigest()[:4]

def read_hashed(file_path: str):
    with open(file_path, encoding='utf-8') as f:
        lines = f.readlines()
    output = []
    for i, line in enumerate(lines, 1):
        output.append(f"{i:4d} | {hash_line(line)} | {line}")
    return "".join(output)

def apply_patch(file_path: str, start_hash: str, end_hash: str, new_content: str):
    with open(file_path, encoding='utf-8') as f:
        lines = f.readlines()

    start_idx, end_idx = -1, -1
    for i, line in enumerate(lines):
        h = hash_line(line)
        if h == start_hash and start_idx == -1:
            start_idx = i
        if h == end_hash and start_idx != -1:
            end_idx = i
            break

    if start_idx == -1 or end_idx == -1:
        return {"success": False, "error": f"Nenalezeny hashe: start={start_hash}, end={end_hash}"}

    replacement = [l + '\n' for l in new_content.splitlines()]
    lines[start_idx:end_idx+1] = replacement

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return {"success": True, "replaced_lines": f"{start_idx+1}-{end_idx+1}"}

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "read":
        print(read_hashed(sys.argv[2]))
    elif mode == "patch":
        res = apply_patch(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        print(json.dumps(res))