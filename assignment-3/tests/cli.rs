//! Runs the bmpflip program on real files.

mod common;

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use bmpflip::Op;
use common::Image;

fn here(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(path)
}

fn tmp(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("cli");
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    let _ = fs::remove_file(&path);
    path
}

fn bmpflip(args: &[&str]) -> i32 {
    let output = Command::new(env!("CARGO_BIN_EXE_bmpflip")).args(args).output().unwrap();
    output.status.code().expect("bmpflip was killed by a signal")
}

fn s(path: &Path) -> &str {
    path.to_str().unwrap()
}

fn first_difference(actual: &[u8], expected: &[u8]) -> Option<String> {
    if actual.len() != expected.len() {
        return Some(format!("size {} instead of {}", actual.len(), expected.len()));
    }
    let at = actual.iter().zip(expected).position(|(a, e)| a != e)?;
    Some(format!("first different byte at offset {at}"))
}

const IMAGES: [&str; 11] = ["f1", "f4", "f8", "f8-topdown", "f16-555", "f16-565", "f24", "f24-dirty-padding", "f24-v5", "f32", "f32-v4-alpha"];

#[test]
fn test_images_both_modes() {
    let mut failures = Vec::new();
    for image in IMAGES {
        let input = here(&format!("images/{image}.bmp"));
        for op in ["h", "v", "hv"] {
            let expected = fs::read(here(&format!("expected/{image}.{op}.bmp"))).unwrap();
            let output = tmp(&format!("{image}.{op}.bmp"));
            let code = bmpflip(&["--memory", "4096", op, s(&input), s(&output)]);
            match code {
                0 => failures.extend(first_difference(&fs::read(&output).unwrap(), &expected).map(|d| format!("bmpflip {op} {image}.bmp: {d}"))),
                _ => failures.push(format!("bmpflip {op} {image}.bmp: exit code {code}")),
            }
            let file = tmp(&format!("{image}.{op}.in-place.bmp"));
            fs::copy(&input, &file).unwrap();
            let code = bmpflip(&["--memory", "4096", "--in-place", op, s(&file)]);
            match code {
                0 => failures.extend(first_difference(&fs::read(&file).unwrap(), &expected).map(|d| format!("bmpflip --in-place {op} {image}.bmp: {d}"))),
                _ => failures.push(format!("bmpflip --in-place {op} {image}.bmp: exit code {code}")),
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn conversions() {
    let mut failures = Vec::new();
    let mut cases: Vec<(String, Option<&str>, &str)> = Vec::new();
    for image in IMAGES {
        for format in ["gray", "16", "24", "32"] {
            cases.push((image.to_string(), None, format));
        }
    }
    cases.push(("f4".into(), Some("hv"), "16"));
    cases.push(("f32-v4-alpha".into(), Some("v"), "gray"));
    cases.push(("f16-565".into(), Some("h"), "24"));
    for (image, op, format) in cases {
        let input = here(&format!("images/{image}.bmp"));
        let name = match op {
            Some(op) => format!("{image}.{op}.to-{format}.bmp"),
            None => format!("{image}.to-{format}.bmp"),
        };
        let output = tmp(&name);
        let mut args = vec!["--memory", "4096", "--to", format];
        args.extend(op);
        args.extend([s(&input), s(&output)]);
        let code = bmpflip(&args);
        let expected = fs::read(here(&format!("expected/{name}"))).unwrap();
        match code {
            0 => failures.extend(first_difference(&fs::read(&output).unwrap(), &expected).map(|d| format!("bmpflip {}: {d}", args[2..].join(" ")))),
            _ => failures.push(format!("bmpflip {}: exit code {code}", args[2..].join(" "))),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn a_larger_file_with_the_default_memory() {
    let image = Image::new(24, 20_000, 300).dirty();
    let input = tmp("larger.bmp");
    fs::write(&input, image.bytes()).unwrap();
    let mut expected = vec![0; image.len() as usize];
    image.expected(&image.header(), Op::Hv, 0, &mut expected);
    let output = tmp("larger.hv.bmp");
    assert_eq!(bmpflip(&["hv", s(&input), s(&output)]), 0);
    assert_eq!(first_difference(&fs::read(&output).unwrap(), &expected), None, "bmpflip hv");
    assert_eq!(bmpflip(&["--in-place", "hv", s(&input)]), 0);
    assert_eq!(first_difference(&fs::read(&input).unwrap(), &expected), None, "bmpflip --in-place hv");
}

#[test]
fn rejected_files() {
    let codes = fs::read_to_string(here("images/bad/exit-codes.txt")).unwrap();
    for line in codes.lines() {
        let (name, code) = line.split_once(' ').unwrap();
        let code: i32 = code.parse().unwrap();
        let input = here(&format!("images/bad/{name}"));
        let output = tmp(&format!("bad-{name}"));
        assert_eq!(bmpflip(&["h", s(&input), s(&output)]), code, "bmpflip h bad/{name}");
        assert!(!output.exists(), "bmpflip h bad/{name} created an output file");
        let file = tmp(&format!("bad-in-place-{name}"));
        fs::copy(&input, &file).unwrap();
        assert_eq!(bmpflip(&["--in-place", "h", s(&file)]), code, "bmpflip --in-place h bad/{name}");
        assert_eq!(fs::read(&file).unwrap(), fs::read(&input).unwrap(), "bmpflip --in-place h bad/{name} changed the file");
        assert_eq!(bmpflip(&["--to", "gray", s(&input), s(&output)]), code, "bmpflip --to gray bad/{name}");
        assert!(!output.exists(), "bmpflip --to gray bad/{name} left an output file");
    }
}

#[test]
fn usage_errors() {
    let f8 = here("images/f8.bmp");
    let f8 = s(&f8);
    let out = tmp("usage.bmp");
    let out = s(&out);
    for args in [
        vec![],
        vec!["h", f8],
        vec!["h", f8, out, "extra"],
        vec!["x", f8, out],
        vec!["H", f8, out],
        vec!["vh", f8, out],
        vec!["--memory", "h", f8, out],
        vec!["--memory", "4095", "h", f8, out],
        vec!["--memory", "lots", "h", f8, out],
        vec!["--memory", "-5000", "h", f8, out],
        vec!["--in-place", "h", f8, out],
        vec!["--in-place", "h"],
        vec!["--to", "12", f8, out],
        vec!["--to", f8, out],
        vec!["--to", "gray", "x", f8, out],
        vec!["--to", "gray", f8],
        vec!["--to", "gray", "--in-place", "h", f8],
    ] {
        assert_eq!(bmpflip(&args), 1, "bmpflip {}", args.join(" "));
    }
    assert!(!Path::new(out).exists(), "a usage error created an output file");
    assert_eq!(bmpflip(&["--memory", "4096", "h", f8, out]), 0, "--memory 4096 is allowed");
}

#[test]
fn file_errors() {
    let missing = here("images/no-such-file.bmp");
    let output = tmp("file-errors.bmp");
    assert_eq!(bmpflip(&["v", s(&missing), s(&output)]), 2, "missing input");
    assert!(!output.exists());
    assert_eq!(bmpflip(&["--in-place", "v", s(&missing)]), 2, "missing file, in place");
    let unwritable = here("images/no-such-folder/out.bmp");
    assert_eq!(bmpflip(&["v", s(&here("images/f8.bmp")), s(&unwritable)]), 6, "output folder does not exist");
}

#[test]
fn output_named_like_the_input() {
    let original = fs::read(here("images/f24.bmp")).unwrap();
    let expected = fs::read(here("expected/f24.h.bmp")).unwrap();
    let file = tmp("same.bmp");
    fs::write(&file, &original).unwrap();
    let code = bmpflip(&["h", s(&file), s(&file)]);
    let after = fs::read(&file).unwrap();
    assert!(
        (code == 1 && after == original) || (code == 0 && after == expected),
        "bmpflip h a.bmp a.bmp: exit code {code}, and the file is {}",
        if after == original { "unchanged" } else if after == expected { "flipped" } else { "damaged" }
    );
}
