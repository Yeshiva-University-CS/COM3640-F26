//! Converts generated images between formats, with and without a flip, with
//! rows that fit in memory and rows that do not. Every case also checks the
//! memory budget.

mod common;

use bmpflip::{Error, Format, Op};
use common::*;

const MEMORY: usize = 4096;

/// Every kind of input `convert` must read.
fn sources(width: u32, height: u32) -> Vec<Image> {
    let n = Image::new;
    vec![
        n(1, width * 24, height),
        n(4, width * 6, height),
        n(4, width * 6, height).colors(5),
        n(8, width * 3, height),
        n(8, width * 3, height).colors(100).top_down(),
        n(16, width * 3 / 2, height),
        n(16, width * 3 / 2, height).bitfields([0xF800, 0x07E0, 0x001F]),
        n(16, width * 3 / 2, height).bitfields([0x0F00, 0x00F0, 0x000F]).dirty(),
        n(24, width, height),
        n(32, width * 3 / 4, height).top_down(),
        n(32, width * 3 / 4, height).bitfields([0x0000_00FF, 0x0000_FF00, 0x00FF_0000]),
        Image { dib: 108, ..n(32, width * 3 / 4 + 1, height) }.bitfields([0x3FF0_0000, 0x000F_FC00, 0x0000_03FF]),
    ]
}

fn cases(width: u32, height: u32, op: Option<Op>) {
    let mut failures = Vec::new();
    let mut total = 0;
    for image in sources(width, height) {
        for format in FORMATS {
            total += 1;
            let what = format!("convert to {format:?}, {op:?}, {}", image.name());
            failures.extend(check(&what, &convert_generated(&image, format, op, MEMORY), MEMORY).err());
        }
    }
    all_ok(failures, total);
}

#[test]
fn short_rows() {
    cases(7, 5, None);
}

#[test]
fn short_rows_flipped() {
    cases(9, 4, Some(Op::Hv));
    cases(10, 3, Some(Op::V));
}

#[test]
fn long_rows() {
    cases(1500, 3, None);
}

#[test]
fn long_rows_flipped() {
    cases(1501, 3, Some(Op::H));
    cases(1366, 2, Some(Op::Hv));
}

#[test]
fn other_budgets() {
    let mut failures = Vec::new();
    let mut total = 0;
    for memory in [5003, 65_536, 100_000] {
        for image in [Image::new(24, 30_000, 3), Image::new(8, 40_001, 2).colors(200), Image::new(16, 999, 99).bitfields([0xF800, 0x07E0, 0x001F])] {
            for (format, op) in [(Format::Gray, None), (Format::Rgb565, Some(Op::V)), (Format::Rgb32, Some(Op::Hv))] {
                total += 1;
                let what = format!("convert to {format:?}, {op:?}, {} with {memory} bytes", image.name());
                failures.extend(check(&what, &convert_generated(&image, format, op, memory), memory).err());
            }
        }
    }
    all_ok(failures, total);
}

fn put(b: &mut [u8], at: usize, bytes: &[u8]) {
    b[at..at + bytes.len()].copy_from_slice(bytes);
}

#[test]
fn rejected_conversions_are_not_written() {
    let le = |v: u32| v.to_le_bytes();
    let indexed = Image::new(4, 9, 3).bytes();
    let fields = Image::new(16, 9, 3).bitfields([0xF800, 0x07E0, 0x001F]).bytes();
    let v4 = Image { dib: 108, ..Image::new(32, 9, 3) }.bitfields([0xFF_0000, 0xFF00, 0xFF]).bytes();
    let mut cases: Vec<(&str, Vec<u8>, &str)> = Vec::new();
    let mut patch = |name, base: &Vec<u8>, at, bytes: &[u8], expected| {
        let mut b = base.clone();
        put(&mut b, at, bytes);
        cases.push((name, b, expected));
    };
    patch("17 colors at 4 bits", &indexed, 46, &le(17), "Unsupported");
    patch("a color table that runs into the pixels", &indexed, 10, &le(14 + 40 + 60), "NotBmp");
    patch("a zero mask", &fields, 58, &le(0), "Unsupported");
    patch("a mask with a gap", &fields, 54, &le(0xF0F0), "Unsupported");
    patch("a mask wider than 16 bits", &fields, 62, &le(0x1_FFFF), "Unsupported");
    patch("masks after the start of the pixels", &fields, 10, &le(54), "NotBmp");
    patch("a gap in a V4 header's mask", &v4, 54, &le(0xFF_00FF), "Unsupported");
    patch("not a BMP at all", &indexed, 0, b"XX", "NotBmp");
    for (name, bytes, expected) in cases {
        let input = MemFile::new(bytes);
        let ((result, output), _) = run(move || {
            let (mut input, mut output) = (input, MemFile::new(Vec::new()));
            (bmpflip::convert(Format::Rgb24, None, &mut input, &mut output, MEMORY), output)
        });
        assert_eq!(format!("{:?}", result.as_ref().err()), format!("Some({expected})"), "convert, {name}");
        assert_eq!(output.stats.writes, 0, "convert wrote to the output for a rejected file: {name}");
    }
}

#[test]
fn conversion_errors() {
    let image = Image::new(8, 3000, 4);
    for calls in [0, 1, 3, 6] {
        let input = FailAfter { inner: Generated::new(&image), calls };
        let (result, _) = run(move || {
            let (mut input, mut output) = (input, MemFile::new(Vec::new()));
            bmpflip::convert(Format::Rgb32, Some(Op::H), &mut input, &mut output, MEMORY)
        });
        assert!(matches!(result, Err(Error::Read(_))), "input fails after {calls} calls: {result:?}");
        let output = FailAfter { inner: MemFile::new(Vec::new()), calls };
        let (result, _) = run(move || {
            let (mut input, mut output) = (Generated::new(&Image::new(8, 3000, 4)), output);
            bmpflip::convert(Format::Rgb32, Some(Op::H), &mut input, &mut output, MEMORY)
        });
        assert!(matches!(result, Err(Error::Write(_))), "output fails after {calls} calls: {result:?}");
    }
    let (input, output) = (Flaky::new(Generated::new(&image), 5), Flaky::new(Checked::converting(&image, Format::Rgb565, Some(Op::V)), 6));
    let ((result, output), peak) = run(move || {
        let (mut input, mut output) = (input, output);
        (bmpflip::convert(Format::Rgb565, Some(Op::V), &mut input, &mut output, MEMORY), output)
    });
    let outcome = Outcome { result, peak, stats: Stats::default(), verdict: output.inner.verdict() };
    check("convert on flaky storage", &outcome, MEMORY).unwrap();
}

/// Checks 9 and 10 combined with the others, and odd files that must be accepted.
#[test]
fn the_order_of_the_conversion_checks() {
    let le = |v: u32| v.to_le_bytes();
    let indexed = Image::new(8, 5, 3).colors(16).bytes();
    let fields = Image::new(32, 5, 3).bitfields([0xFF_0000, 0xFF00, 0xFF]).bytes();
    let edit = |base: &Vec<u8>, changes: &[(usize, [u8; 4])]| {
        let mut b = base.clone();
        for (at, v) in changes {
            b[*at..*at + 4].copy_from_slice(v);
        }
        b
    };
    let cases: Vec<(&str, Vec<u8>, &str)> = vec![
        ("a bad signature and too many colors", edit(&indexed, &[(0, *b"XXXX"), (46, le(999))]), "NotBmp"),
        ("compression 1 and too many colors", edit(&indexed, &[(30, le(1)), (46, le(999))]), "Unsupported"),
        ("truncated, with too many colors", { let mut b = edit(&indexed, &[(46, le(999))]); b.truncate(b.len() - 1); b }, "Truncated"),
        ("too many colors, and their table overlaps the pixels", edit(&indexed, &[(46, le(257))]), "Unsupported"),
        ("exactly 256 colors, overlapping the pixels", edit(&indexed, &[(46, le(256))]), "NotBmp"),
        ("one color", edit(&indexed, &[(46, le(1))]), "Ok"),
        ("a bad mask and truncated", { let mut b = edit(&fields, &[(54, le(0))]); b.truncate(b.len() - 1); b }, "Truncated"),
        ("a 32-bit mask", edit(&fields, &[(54, le(u32::MAX)), (58, le(1)), (62, le(1))]), "Ok"),
        ("masks after the pixel offset and a bad mask", edit(&fields, &[(10, le(54)), (54, le(0))]), "NotBmp"),
        ("overlapping masks are allowed", edit(&fields, &[(54, le(0xFFFF)), (58, le(0xFF))]), "Ok"),
    ];
    let mut failures = Vec::new();
    for (name, bytes, expected) in &cases {
        let input = MemFile::new(bytes.clone());
        let (result, _) = run(move || {
            let (mut input, mut output) = (input, MemFile::new(Vec::new()));
            bmpflip::convert(Format::Gray, None, &mut input, &mut output, 4096)
        });
        let got = match result {
            Ok(()) => "Ok".to_string(),
            Err(e) => format!("{e:?}"),
        };
        if got != *expected {
            failures.push(format!("{name}: {got}, expected {expected}"));
        }
    }
    all_ok(failures, cases.len());
}
