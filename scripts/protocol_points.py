"""Instruction boundaries reviewed against official v1.17.0 x86_64 symbols.

Offsets are FILE offsets, not runtime addresses. Only this exact ELF is accepted.
No Rust channel payload or unstable ABI layout is decoded.
"""
import hashlib

BINARY_SHA256='99ad0f5cd0514a88aad0e9ae8cfdb3cc3b4ab9d190e1194602406c786b5de7a5'
BUILD_ID='43fcf6af0eede7e885a4376d361962fe6d7bfb51'
POINTS=[('send_enter','p',0x12a400),('send_return','r',0x12a400),
        ('wait_enter','p',0x12b060),('wait_return','r',0x12b060),
        ('drop_enter','p',0x13bf40),('drop_return','r',0x13bf40),
        ('run_return','p',0x111806),('disconnected','p',0x13c152),
        ('join_enter','p',0x13c159),('join_return','p',0x13c15f)]

def verify_binary(path):
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=BINARY_SHA256:
        raise ValueError('Protocol offsets require the exact reviewed official v1.17.0 ELF')
    if raw[0x111806]!=0xc3 or raw[0x13c159:0x13c15f]!=bytes.fromhex('ff15699e1b00'):
        raise ValueError('Reviewed return/join instruction mismatch')
    return [{'name':name,'kind':kind,'file_offset':hex(offset),'instruction_bytes':raw[offset:offset+8].hex()} for name,kind,offset in POINTS]
