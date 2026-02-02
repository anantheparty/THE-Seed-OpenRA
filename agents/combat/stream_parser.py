import json
import logging
from typing import Generator, List, Tuple, Optional

logger = logging.getLogger(__name__)

class StreamParser:
    """
    Parses streaming JSON output from LLM to extract attack pairs in real-time.
    Expects format: [[id1, id2], [id3, id4], ...]
    Robust against truncation.
    """
    
    def __init__(self):
        self.buffer = ""
        self.array_started = False
        self.depth = 0
        self.scan_idx = 0
        self.inner_start = None
        
    def parse_chunk(self, chunk: str) -> List[Tuple[int, int]]:
        """
        Accumulates chunk and returns list of newly found valid pairs.
        """
        self.buffer += chunk
        pairs = []

        if not self.array_started:
            bracket_pos = self.buffer.find('[')
            if bracket_pos != -1:
                self.array_started = True
                self.depth = 1
                self.scan_idx = bracket_pos + 1
                self.inner_start = None

        processed_len = 0
        completed_array = False

        while self.array_started and self.scan_idx < len(self.buffer):
            ch = self.buffer[self.scan_idx]
            if ch == '[':
                self.depth += 1
                if self.depth == 2:
                    self.inner_start = self.scan_idx
            elif ch == ']':
                if self.depth == 2 and self.inner_start is not None:
                    inner_end = self.scan_idx
                    candidate = self.buffer[self.inner_start:inner_end + 1]
                    try:
                        arr = json.loads(candidate)
                        if (
                            isinstance(arr, list)
                            and len(arr) == 2
                            and all(isinstance(x, int) for x in arr)
                        ):
                            pairs.append((arr[0], arr[1]))
                    except Exception:
                        pass
                    self.inner_start = None
                    processed_len = inner_end + 1
                self.depth -= 1
                if self.depth == 0:
                    completed_array = True
                    break
            self.scan_idx += 1

        if completed_array:
            self.buffer = ""
            self.scan_idx = 0
            self.array_started = False
            self.depth = 0
            self.inner_start = None
            return pairs

        if processed_len > 0:
            self.buffer = self.buffer[processed_len:]
            self.scan_idx = max(0, self.scan_idx - processed_len)

        if not self.array_started and len(self.buffer) > 4096:
            self.buffer = self.buffer[-4096:]

        return pairs

    def reset(self):
        self.buffer = ""
        self.array_started = False
        self.depth = 0
        self.scan_idx = 0
        self.inner_start = None
