//! Storage that behaves like `pread` and `pwrite` at their worst, failing
//! storage, files the library must reject without writing anything, and the
//! order of the header checks.

mod common;

use bmpflip::{Error, Op, read_info};
use common::*;

const MEMORY: usize = 4096;

fn some_images() -> Vec<Image> {
    vec![Image::new(24, 3000, 3), Image::new(1, 40_003, 3).top_down(), Image::new(8, 100, 50), Image::new(32, 7, 2).dirty()]
}

#[test]
fn short_and_interrupted_reads_and_writes() {
    let mut failures = Vec::new();
    let mut total = 0;
    for (i, image) in some_images().iter().enumerate() {
        for op in OPS {
            total += 2;
            let (input, output) = (Flaky::new(Generated::new(image), i as u64 + 7), Flaky::new(Checked::new(image, op), i as u64 + 99));
            let ((result, output), peak) = run(move || {
                let (mut input, mut output) = (input, output);
                (bmpflip::flip(op, &mut input, &mut output, MEMORY), output)
            });
            let outcome = Outcome { result, peak, stats: Stats::default(), verdict: output.inner.verdict() };
            failures.extend(check(&format!("flip {op:?} {} on flaky storage", image.name()), &outcome, MEMORY).err());

            let file = Flaky::new(MemFile::new(image.bytes()), i as u64 + 3);
            let ((result, file), peak) = run(move || {
                let mut file = file;
                (bmpflip::flip_in_place(op, &mut file, MEMORY), file)
            });
            let outcome = Outcome { result, peak, stats: Stats::default(), verdict: file.inner.verdict(image, op) };
            failures.extend(check(&format!("flip_in_place {op:?} {} on flaky storage", image.name()), &outcome, MEMORY).err());
        }
    }
    all_ok(failures, total);
}

#[test]
fn failing_storage_is_an_error_not_a_panic() {
    let image = Image::new(24, 3000, 4);
    for calls in [0, 1, 2, 5, 9] {
        let input = FailAfter { inner: Generated::new(&image), calls };
        let (result, _) = run(move || {
            let (mut input, mut output) = (input, MemFile::new(Vec::new()));
            bmpflip::flip(Op::Hv, &mut input, &mut output, MEMORY)
        });
        assert!(matches!(result, Err(Error::Read(_))), "input fails after {calls} calls: flip returned {result:?}");

        let output = FailAfter { inner: MemFile::new(Vec::new()), calls };
        let (result, _) = run(move || {
            let (mut input, mut output) = (Generated::new(&Image::new(24, 3000, 4)), output);
            bmpflip::flip(Op::Hv, &mut input, &mut output, MEMORY)
        });
        assert!(matches!(result, Err(Error::Write(_))), "output fails after {calls} calls: flip returned {result:?}");
    }
    for calls in [3, 4, 6] {
        let file = FailAfter { inner: MemFile::new(image.bytes()), calls };
        let (result, _) = run(move || {
            let mut file = file;
            bmpflip::flip_in_place(Op::V, &mut file, MEMORY)
        });
        assert!(matches!(result, Err(Error::Read(_) | Error::Write(_))), "in place, storage fails after {calls} calls: {result:?}");
    }
}

#[test]
fn a_file_shorter_than_it_claims_is_truncated() {
    for image in [Image::new(24, 3000, 4), Image { trailing: 10, ..Image::new(8, 10, 10) }] {
        for extra in [1, 1000, 1 << 40] {
            let input = LongerThanItIs { inner: MemFile::new(image.bytes()[..image.len() as usize - 5].to_vec()), extra: extra + 5 };
            let (result, _) = run(move || {
                let (mut input, mut output) = (input, MemFile::new(Vec::new()));
                bmpflip::flip(Op::H, &mut input, &mut output, MEMORY)
            });
            assert!(matches!(result, Err(Error::Truncated)), "{}, 5 bytes short, claiming {extra} more: flip returned {result:?}", image.name());
        }
    }
}

fn info_of(bytes: Vec<u8>) -> Result<bmpflip::Info, Error> {
    read_info(&mut MemFile::new(bytes))
}

#[test]
fn read_info_fields() {
    let image = Image { gap: 12, ..Image::new(4, 31, 45).top_down() };
    let info = info_of(image.bytes()).unwrap();
    assert_eq!((info.width, info.height, info.bits_per_pixel, info.top_down, info.pixel_offset, info.stride), (31, 45, 4, true, 66, 16));
    let huge = Image::new(24, 100_000, 100_000);
    let mut header = huge.header();
    header.resize(64, 0);
    let file = LongerThanItIs { inner: MemFile::new(header), extra: huge.len() - 64 };
    let (info, _) = run(move || {
        let mut file = file;
        read_info(&mut file)
    });
    let info = info.unwrap();
    assert_eq!((info.stride, info.pixel_offset), (300_000, 54), "a 30 GB file");
}

fn put(b: &mut [u8], at: usize, bytes: &[u8]) {
    b[at..at + bytes.len()].copy_from_slice(bytes);
}

#[test]
fn rejected_files_are_not_written() {
    let base = Image { gap: 12, ..Image::new(24, 5, 3) }.bytes();
    let mut cases: Vec<(&str, Vec<u8>, &str)> = vec![
        ("empty", vec![], "NotBmp"),
        ("17 bytes", base[..17].to_vec(), "NotBmp"),
        ("ends inside the DIB header", base[..40].to_vec(), "NotBmp"),
        ("one byte short", base[..base.len() - 1].to_vec(), "Truncated"),
    ];
    let mut patch = |name, at, bytes: &[u8], expected| {
        let mut b = base.clone();
        put(&mut b, at, bytes);
        cases.push((name, b, expected));
    };
    patch("signature BA", 0, b"BA", "NotBmp");
    patch("header size 12", 14, &12u32.to_le_bytes(), "Unsupported");
    patch("width 0", 18, &0u32.to_le_bytes(), "NotBmp");
    patch("height 0", 22, &0u32.to_le_bytes(), "NotBmp");
    patch("7 bits per pixel", 28, &7u16.to_le_bytes(), "Unsupported");
    patch("compression 1", 30, &1u32.to_le_bytes(), "Unsupported");
    patch("compression 3 at 24 bits", 30, &3u32.to_le_bytes(), "Unsupported");
    patch("pixel offset 53", 10, &53u32.to_le_bytes(), "NotBmp");
    patch("huge width", 18, &i32::MAX.to_le_bytes(), "Truncated");
    patch("height i32::MIN", 22, &i32::MIN.to_le_bytes(), "Truncated");
    for (name, bytes, expected) in cases {
        let got = info_of(bytes.clone());
        assert_eq!(format!("{:?}", got.as_ref().err()), format!("Some({expected})"), "read_info, {name}");

        let input = MemFile::new(bytes.clone());
        let (result, output) = run(move || {
            let (mut input, mut output) = (input, MemFile::new(Vec::new()));
            (bmpflip::flip(Op::H, &mut input, &mut output, MEMORY), output)
        })
        .0;
        assert!(result.is_err(), "flip accepted a file: {name}");
        assert_eq!(output.stats.writes, 0, "flip wrote to the output for a rejected file: {name}");

        let file = MemFile::new(bytes.clone());
        let (result, file) = run(move || {
            let mut file = file;
            (bmpflip::flip_in_place(Op::Hv, &mut file, MEMORY), file)
        })
        .0;
        assert!(result.is_err(), "flip_in_place accepted a file: {name}");
        assert_eq!(file.stats.writes, 0, "flip_in_place wrote to a rejected file: {name}");
    }
}

fn info(bytes: Vec<u8>) -> String {
    match info_of(bytes) {
        Ok(_) => "Ok".into(),
        Err(e) => format!("{e:?}"),
    }
}

fn base() -> Vec<u8> {
    Image { gap: 12, ..Image::new(24, 5, 3) }.bytes()
}

fn with(changes: &[(usize, &[u8])]) -> Vec<u8> {
    let mut b = base();
    for (at, bytes) in changes {
        b[*at..*at + bytes.len()].copy_from_slice(bytes);
    }
    b
}

/// Files with one fault or several: the first check that fails decides the error.
/// Some odd files must be accepted.
#[test]
fn the_order_of_the_header_checks() {
    let le = |v: u32| v.to_le_bytes();
    let cases: Vec<(&str, Vec<u8>, &str)> = vec![
        ("bad signature and a 12-byte header", with(&[(0, b"bm"), (14, &le(12))]), "NotBmp"),
        ("unknown header size, file ends early", { let mut b = with(&[(14, &le(64))]); b.truncate(30); b }, "Unsupported"),
        ("width 0 and 7 bits per pixel", with(&[(18, &le(0)), (28, &7u16.to_le_bytes())]), "NotBmp"),
        ("7 bits per pixel and a bad offset", with(&[(28, &7u16.to_le_bytes()), (10, &le(0))]), "Unsupported"),
        ("compression 1 and a bad offset", with(&[(30, &le(1)), (10, &le(0))]), "Unsupported"),
        ("bad offset and truncated", { let mut b = with(&[(10, &le(20))]); b.truncate(60); b }, "NotBmp"),
        ("offset 54 is allowed", with(&[(10, &le(54))]), "Ok"),
        ("offset at the end of the file", { let b = base(); let n = b.len() as u32; with(&[(10, &le(n))]) }, "Truncated"),
        ("file-size field wrong", with(&[(2, &le(3))]), "Ok"),
        ("planes field wrong", with(&[(26, &9u16.to_le_bytes())]), "Ok"),
        ("negative width", with(&[(18, &le(-5i32 as u32))]), "NotBmp"),
        ("width i32::MIN", with(&[(18, &le(i32::MIN as u32))]), "NotBmp"),
        ("16 bits, compression 3", with(&[(28, &16u16.to_le_bytes()), (30, &le(3)), (18, &le(7))]), "Ok"),
        ("32 bits, compression 6", with(&[(28, &32u16.to_le_bytes()), (30, &le(6))]), "Unsupported"),
        ("huge width and height, 32 bits", with(&[(18, &le(i32::MAX as u32)), (22, &le(i32::MAX as u32)), (28, &32u16.to_le_bytes())]), "Truncated"),
    ];
    let mut failures = Vec::new();
    for (name, bytes, expected) in &cases {
        let got = info(bytes.clone());
        if got != *expected {
            failures.push(format!("{name}: {got}, expected {expected}"));
        }
    }
    all_ok(failures, cases.len());
}
