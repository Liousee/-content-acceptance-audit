#!/usr/bin/env python3
"""极简 CDP WebSocket 客户端(纯标准库, 无三方依赖) —— 用于读取已打开标签页的页面文本/标题

为什么需要: Chrome 的 /json/list HTTP 接口只给 title/url, 页面没渲染出标题时无法判定;
           登录态下抖音的作品页标题常常不写入 tab title, 必须直接读 document。
协议: RFC6455 客户端帧(must be masked) + 仅处理 text 帧与 ping/pong/close。
用法: from cdp_ws import probe_page
      probe_page(ws_url) -> {'title':..,'ready':..,'text':..} or None
"""
import base64
import json
import os
import re
import socket
import struct


class WS:
    def __init__(self, url, timeout=20):
        m = re.match(r'ws://([^:/]+):(\d+)(/.*)$', url)
        if not m:
            raise ValueError(f'bad ws url: {url}')
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n'
               f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            d = self.s.recv(4096)
            if not d:
                raise ConnectionError('handshake closed')
            buf += d
        head, self.buf = buf.split(b'\r\n\r\n', 1)
        if b'101' not in head.split(b'\r\n')[0]:
            raise ConnectionError(f'handshake failed: {head[:80]!r}')

    def _read(self, n):
        while len(self.buf) < n:
            d = self.s.recv(max(4096, n - len(self.buf)))
            if not d:
                raise EOFError('eof')
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode, payload: bytes):
        hdr = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.s.sendall(hdr + mask + masked)

    def send_text(self, text):
        self._frame(0x1, text.encode())

    def recv_text(self):
        while True:
            b1 = self._read(2)
            op = b1[0] & 0x0F
            ln = b1[1] & 0x7F
            if ln == 126:
                ln = struct.unpack('>H', self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack('>Q', self._read(8))[0]
            payload = self._read(ln)
            if op == 0x9:                      # ping -> pong
                self._frame(0xA, payload)
                continue
            if op == 0xA:                      # pong
                continue
            if op == 0x8:                      # close
                raise EOFError('closed')
            if op in (0x1, 0x2):
                return payload.decode('utf-8', 'ignore')

    def close(self):
        try:
            self._frame(0x8, b'')
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass


JS_PROBE = ("JSON.stringify({t:document.title,"
            "ready:document.readyState,"
            "url:location.href,"
            "desc:((document.querySelector('meta[name=description]')||{}).content||''),"
            "h1:((document.querySelector('h1')||{}).innerText||''),"
            "data:(document.body?document.body.innerText:'').replace(/<[^>]*>/g,'')"
            ".replace(/[\\t\\n\\r]+/g,' ')"
            ".replace(/^[\\s\\S]*?(连播|倍速|分享|举报|下载)/,'')"
            ".slice(0,600)})")


def probe_page(ws_url, timeout=20, expr=JS_PROBE):
    """返回 {'title','ready','url','text'} ; 失败返回 None"""
    w = None
    try:
        w = WS(ws_url, timeout=timeout)
        w.send_text(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': expr, 'returnByValue': True}}))
        for _ in range(60):
            msg = json.loads(w.recv_text())
            if msg.get('id') == 1:
                r = msg.get('result', {}).get('result', {})
                v = r.get('value')
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        return {'text': v}
                return None
    except Exception:
        return None
    finally:
        if w:
            w.close()
    return None


if __name__ == '__main__':
    import sys
    print(json.dumps(probe_page(sys.argv[1]), ensure_ascii=False, indent=1))
