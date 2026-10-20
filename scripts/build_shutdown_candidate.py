#!/usr/bin/env python3
"""Build matched stock/candidate VMMs without replacing the installed release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

COMMIT='95f868c8e345b1cc8faccd1a3c910b4989dc3f58'
ARCHIVE_SHA='27280787407229c412901dfcf74cd378f1336010e71050aba43ace51cd4a0f4f'
IMAGE='rust@sha256:b5a086f64ffecaa4e283063184770107915756739598173e1f5712d6b34b84d0'
ORIGINAL='const VCPU_JOIN_TIMEOUT: Duration = Duration::from_secs(1);'
CANDIDATE='const VCPU_JOIN_TIMEOUT: Duration = Duration::from_secs(5);'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def static_properties(binary):
    headers=subprocess.check_output(['readelf','-l',str(binary)],text=True)
    dynamic=subprocess.check_output(['readelf','-d',str(binary)],text=True)
    return {'has_pt_interp':'INTERP' in headers,'has_dt_needed':'(NEEDED)' in dynamic,'file':subprocess.check_output(['file','-b',str(binary)],text=True).strip(),'sha256':sha(binary)}

def require_static(properties):
    if properties['has_pt_interp'] or properties['has_dt_needed']:raise ValueError('executable requires a dynamic interpreter or library')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='New build directory')
    parser.add_argument('--reuse-from', help='Optional retained build directory, recorded in provenance')
    parser.add_argument('--jailer',required=True,help='Existing, reviewed v1.17.0 jailer')
    args=parser.parse_args();out=Path(args.output).resolve();out.mkdir(exist_ok=False)
    shutil.copyfile(Path(__file__).resolve(),out/'build-script.py')
    report={'schema':1,'status':'failed','commit':COMMIT,'source_url':f'https://github.com/firecracker-microvm/firecracker/archive/{COMMIT}.tar.gz','archive_sha256_expected':ARCHIVE_SHA,'builder_image':IMAGE,'variants':{}}
    try:
        repo=Path(__file__).resolve().parents[1]
        base_profile=json.loads((repo/'config/jailer-profile.json').read_text())
        if sha(Path(args.jailer).resolve(strict=True))!=base_profile['jailer_sha256']:raise ValueError('unreviewed jailer hash')
        (out/'profile-base.json').write_text(json.dumps(base_profile,indent=2)+'\n')
        dockerfile=repo/'config/shutdown-builder.Dockerfile'
        shutil.copyfile(dockerfile,out/'Dockerfile')
        report['builder_dockerfile_sha256']=sha(dockerfile)
        subprocess.run(['docker','build','--iidfile',str(out/'builder-image-id'),'--file',str(dockerfile),str(dockerfile.parent)],check=True,stdout=(out/'builder.log').open('w'),stderr=subprocess.STDOUT,timeout=600)
        builder=(out/'builder-image-id').read_text().strip()
        report['builder_image_id']=builder
        resolved=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',builder],text=True).strip()
        if resolved!=builder:raise ValueError('builder image ID mismatch')
        report['resolved_builder_image_id']=resolved
        (out/'static-probe.rs').write_text('fn main(){println!("static probe ok");}\n')
        subprocess.run(['docker','run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}','--mount',f'type=bind,src={out},dst=/work',builder,'rustc','--target','x86_64-unknown-linux-musl','-C','linker=cc','-C','target-feature=+crt-static','/work/static-probe.rs','-o','/work/static-probe'],check=True,timeout=60)
        report['static_link_probe']=static_properties(out/'static-probe')
        require_static(report['static_link_probe'])
        report['static_link_probe']['execution']=subprocess.check_output([str(out/'static-probe')],text=True).strip()
        report['artifact_validation']={}
        archive=out/'source.tar.gz'
        urllib.request.urlretrieve(report['source_url'],archive)
        report['archive_sha256_observed']=sha(archive)
        if report['archive_sha256_observed']!=ARCHIVE_SHA:raise ValueError('source checksum mismatch')
        with tarfile.open(archive) as tar:tar.extractall(out,filter='data')
        source=out/f'firecracker-{COMMIT}';source.rename(out/'source');source=out/'source'
        if args.reuse_from:
            previous=Path(args.reuse_from).resolve(strict=True)
            if sha(previous/'source.tar.gz')!=ARCHIVE_SHA:raise ValueError('cache source mismatch')
            report['reused_build_directory']=str(previous)
            for old,new in [(previous/'cargo-cache',out/'cargo-cache'),(previous/'source/build',source/'build')]:
                if old.exists():shutil.copytree(old,new)
        target=source/'src/vmm/src/vstate/vcpu.rs'
        original=target.read_text()
        if original.count(ORIGINAL)!=1:raise ValueError('unexpected timeout source')
        report['cargo_lock_sha256']=sha(source/'Cargo.lock')
        for variant in ['stock','candidate']:
            target.write_text(original if variant=='stock' else original.replace(ORIGINAL,CANDIDATE))
            dest=out/variant;dest.mkdir()
            command=['docker','run','--rm','--name','fc-build-'+out.name+'-'+variant,'--cap-drop','ALL','--security-opt','no-new-privileges','--memory','3g','--memory-swap','3g','--cpus','2','--pids-limit','256','--user',f'{os.getuid()}:{os.getgid()}','--mount',f'type=bind,src={out},dst=/work','--workdir','/work/source','--env','CARGO_HOME=/work/cargo-cache','--env','CARGO_NET_GIT_FETCH_WITH_CLI=false','--env','RUSTUP_TOOLCHAIN=1.97.0-x86_64-unknown-linux-gnu',builder,'sh','-c','rustc --version; cargo --version; gcc --version; dpkg-query -W libclang-dev libclang-14-dev musl-tools linux-libc-dev libseccomp-dev; cargo build --locked --release --target x86_64-unknown-linux-musl -p firecracker --jobs 2']
            try:
                with (dest/'build.log').open('wb') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
            finally:
                subprocess.run(['docker','rm','-f','fc-build-'+out.name+'-'+variant],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            log_text=(dest/'build.log').read_text(errors='replace')
            if 'Using empty default seccomp policy' in log_text or 'No default seccomp policy' in log_text:raise ValueError('build used an empty fallback seccomp policy')
            filters=list((source/'build/cargo_target/x86_64-unknown-linux-musl/release/build').glob('firecracker-*/out/seccomp_filter.bpf'))
            filter_hashes=sorted({sha(path) for path in filters})
            if len(filter_hashes)!=1:raise ValueError('missing or ambiguous generated seccomp policy')
            shutil.copyfile(source/'build/cargo_target/x86_64-unknown-linux-musl/release/firecracker',dest/'firecracker');(dest/'firecracker').chmod(0o755)
            shutil.copyfile(Path(args.jailer).resolve(strict=True),dest/'jailer');(dest/'jailer').chmod(0o755)
            validation=static_properties(dest/'firecracker')
            report['artifact_validation'][variant]=validation
            (out/'static-validation.json').write_text(json.dumps(report['artifact_validation'],indent=2)+'\n')
            require_static(validation)
            validation['version']=subprocess.check_output([str(dest/'firecracker'),'--version'],text=True,timeout=10).strip()
            if 'Firecracker v1.17.0' not in validation['version']:raise ValueError('unexpected VMM version')
            validation['status']='passed'
            (out/'static-validation.json').write_text(json.dumps(report['artifact_validation'],indent=2)+'\n')
            profile=dict(base_profile,firecracker_sha256=sha(dest/'firecracker'),investigation_matrix='host-load',diagnostic_join_timeout_seconds=1 if variant=='stock' else 5,diagnostic_repetitions=10,suite_timeout=600)
            (dest/'profile.json').write_text(json.dumps(profile,indent=2)+'\n')
            report['variants'][variant]={'seccomp_filter_sha256':filter_hashes[0],'seccomp_source_sha256':sha(source/'resources/seccomp/x86_64-unknown-linux-musl.json'),'profile_sha256':sha(dest/'profile.json'),'firecracker_sha256':sha(dest/'firecracker'),'jailer_sha256':sha(dest/'jailer'),'vcpu_source_sha256':sha(target),'build_log_sha256':sha(dest/'build.log'),'join_timeout_seconds':1 if variant=='stock' else 5,'command':command}
        if len({v['seccomp_filter_sha256'] for v in report['variants'].values()})!=1:raise ValueError('A/B seccomp filters differ')
        report['patch']={'before':ORIGINAL,'after':CANDIDATE,'scope':'bounded timeout only; no changes to isolation or seccomp source'}
        report['status']='passed'
    except Exception as exc:report['failure']=type(exc).__name__+': '+str(exc)
    finally:(out/'build-results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'output':str(out),'status':report['status'],'failure':report.get('failure')},indent=2))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
