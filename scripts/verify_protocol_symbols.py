#!/usr/bin/env python3
"""Pin protocol points to the reviewed ELF, matching debug symbols and source."""
import argparse,hashlib,json,re,subprocess,tarfile
from pathlib import Path
from protocol_points import BINARY_SHA256,BUILD_ID,verify_binary

SOURCE_SHA='27280787407229c412901dfcf74cd378f1336010e71050aba43ace51cd4a0f4f'

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for field in ('binary','debug','source-archive','output'):p.add_argument('--'+field,required=True)
 a=p.parse_args();binary=Path(a.binary);debug=Path(a.debug);archive=Path(a.source_archive)
 def tool(*args):return subprocess.check_output(args,text=True)
 for file in (binary,debug):
  assert BUILD_ID in tool('readelf','-n',str(file)), 'Build ID mismatch'
 assert hashlib.sha256(archive.read_bytes()).hexdigest()==SOURCE_SHA,'Source archive mismatch'
 source={}
 with tarfile.open(archive) as tar:
  for tail in ('src/vmm/src/vstate/vcpu.rs','src/vmm/src/vstate/vm.rs'):
   entries=[m for m in tar.getmembers() if m.name.endswith('/'+tail)]
   assert len(entries)==1
   raw=tar.extractfile(entries[0]).read();text=raw.decode()
   source[tail]={'sha256':hashlib.sha256(raw).hexdigest(),'join_timeout_constant':next((l for l in text.splitlines() if l.startswith('const VCPU_JOIN_TIMEOUT:')),None)}
 assert 'from_secs(1)' in source['src/vmm/src/vstate/vcpu.rs']['join_timeout_constant']
 points=verify_binary(binary)
 for point in points:
  point['symbol']=tool('addr2line','-f','-C','-e',str(debug),point['file_offset']).splitlines()[0]
  point['disassembly']=tool('objdump','-d','--start-address='+point['file_offset'],'--stop-address='+hex(int(point['file_offset'],16)+8),str(binary)).split('Disassembly of section .text:')[-1].strip()
 relocation=[l for l in tool('readelf','-rW',str(binary)).splitlines() if '00000000002f5fc8' in l]
 join=[l for l in tool('nm','-C',str(debug)).splitlines() if l.endswith(' W pthread_join')]
 assert len(relocation)==1 and relocation[0].endswith('2736d2') and len(join)==1 and join[0].startswith('00000000002736d2')
 report={'binary_sha256':BINARY_SHA256,'debug_sha256':hashlib.sha256(debug.read_bytes()).hexdigest(),'matching_build_id':BUILD_ID,'source_commit':'95f868c8e345b1cc8faccd1a3c910b4989dc3f58','source_archive_sha256':SOURCE_SHA,'source':source,'points':points,'program_headers':tool('readelf','-lW',str(binary)),'join_got_relocation':relocation,'join_symbol':join,'limitations':['Debug information resolves function names but not detailed Rust source lines here. Interior points were reviewed with disassembly.','Executable PT_LOAD file offsets equal virtual addresses in this exact ELF. Offsets must not be reused for another build.','No Rust channel payload, response discriminant, or object pointer is decoded by the probes. disconnected labels the branch that proceeds to pthread_join.']}
 Path(a.output).write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
