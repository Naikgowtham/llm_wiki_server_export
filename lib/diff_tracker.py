import difflib
import re

def get_diff_deltas(old_text: str, new_text: str, context_lines: int = 2) -> str:
    """Identify blocks of text that were added, modified, or deleted, with surrounding context."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    deltas = []
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'insert', 'delete'):
            start = max(0, j1 - context_lines)
            end = min(len(new_lines), j2 + context_lines)
            
            # Find the nearest preceding heading
            heading_line = None
            for k in range(start - 1, -1, -1):
                if re.match(r'^#+\s', new_lines[k]):
                    heading_line = new_lines[k]
                    break
            
            # Construct diff block with context and clear indicators
            pre_context = "\n".join(new_lines[start:j1])
            post_context = "\n".join(new_lines[j2:end])
            
            action_text = ""
            if tag == 'delete':
                action_text = "[- DELETED: " + " ".join(old_lines[i1:i2]) + " -]"
            elif tag == 'replace':
                action_text = "[- DELETED: " + " ".join(old_lines[i1:i2]) + " -]\n[+ INSERTED: " + "\n".join(new_lines[j1:j2]) + " +]"
            else: # insert
                action_text = "[+ INSERTED: " + "\n".join(new_lines[j1:j2]) + " +]"
                
            parts = []
            if pre_context: parts.append(pre_context)
            parts.append(action_text)
            if post_context: parts.append(post_context)
            
            delta_chunk = "\n".join(parts)
            
            if heading_line:
                delta_chunk = f"{heading_line}\n...\n{delta_chunk}"
                
            deltas.append(delta_chunk)
            
    return "\n\n...\n\n".join(deltas)
