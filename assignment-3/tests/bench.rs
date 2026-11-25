//! I/O cost, and the full-size tests.
//!
//!   cargo test --release --test bench -- --nocapture             the cost table
//!   cargo test --release --test bench -- --ignored --nocapture   100k-pixel images (minutes)
//!
//! The cost of a run is the bytes read and written plus 64 KiB per call
//! (`Stats::cost` in common/mod.rs). Each scenario shows your cost next to the
//! reference solution's; when I grade, I compare the two on scenarios like these.

mod common;

use bmpflip::{Format, Op};
use common::*;

enum Job {
    Flip(Op),
    InPlace(Op),
    Convert(Format, Option<Op>),
}

fn perform(job: &Job, image: &Image, memory: usize) -> Outcome {
    match *job {
        Job::Flip(op) => flip_generated(image, op, memory),
        Job::InPlace(op) => flip_in_memory(image, op, memory),
        Job::Convert(format, op) => convert_generated(image, format, op, memory),
    }
}

fn scenarios() -> Vec<(&'static str, Image, Job, usize, u64)> {
    use Job::*;
    vec![
        ("small rows, h", Image::new(24, 3000, 400), Flip(Op::H), 100_000, 12246414),
        ("small rows, v", Image::new(24, 3000, 400), Flip(Op::V), 100_000, 12246414),
        ("small rows, v, in place", Image::new(24, 3000, 400), InPlace(Op::V), 100_000, 17751330),
        ("small rows, hv, in place", Image::new(8, 999, 999), InPlace(Op::Hv), 100_000, 4553938),
        ("tiny rows, hv", Image::new(1, 50, 20_000), Flip(Op::Hv), 100_000, 778910),
        ("rows over half the memory, v, in place", Image::new(24, 20_000, 60), InPlace(Op::V), 100_000, 22994210),
        ("long rows, h", Image::new(24, 50_000, 40), Flip(Op::H), 100_000, 22682510),
        ("long rows, hv, in place", Image::new(24, 50_000, 40), InPlace(Op::Hv), 100_000, 33037090),
        ("long rows, h, in place", Image::new(32, 40_000, 30), InPlace(Op::H), 100_000, 21462050),
        ("long 1-bit rows, h, in place", Image::new(1, 100_003, 64), InPlace(Op::H), 4096, 60386338),
        ("long 4-bit rows, hv", Image::new(4, 20_001, 50), Flip(Op::Hv), 5000, 20858028),
        ("24 to gray", Image::new(24, 3000, 400), Convert(Format::Gray, None), 100_000, 10961504),
        ("8 to 32, v", Image::new(8, 3000, 400), Convert(Format::Rgb32, Some(Op::V)), 100_000, 13493228),
        ("long rows, 565 to 24, hv", Image::new(16, 60_000, 30).bitfields([0xF800, 0x07E0, 0x001F]), Convert(Format::Rgb24, Some(Op::Hv)), 100_000, 21648556),
        ("long 1-bit rows to 565", Image::new(1, 100_003, 20), Convert(Format::Rgb565, None), 4096, 105568956),
    ]
}

#[test]
fn io_cost() {
    let mut failures = Vec::new();
    println!("\n{:<42} {:>8} {:>14} {:>14} {:>14} {:>7}", "scenario", "calls", "bytes", "cost", "reference", "ratio");
    for (name, image, job, memory, reference) in scenarios() {
        let outcome = perform(&job, &image, memory);
        if let Err(e) = check(name, &outcome, memory) {
            failures.push(e);
            continue;
        }
        let st = outcome.stats;
        let cost = st.cost();
        println!(
            "{name:<42} {:>8} {:>14} {cost:>14} {reference:>14} {:>7.2}",
            st.reads + st.writes,
            st.bytes_read + st.bytes_written,
            cost as f64 / reference as f64
        );
        println!("COST\t{name}\t{cost}");
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

fn full_size(image: Image, job: Job, memory: usize) {
    let started = std::time::Instant::now();
    let outcome = perform(&job, &image, memory);
    let what = format!("{} with {memory} bytes", image.name());
    check(&what, &outcome, memory).unwrap();
    println!("{what}: {:.1} GB moved in {:.0?}, {} calls", (outcome.stats.bytes_read + outcome.stats.bytes_written) as f64 / 1e9, started.elapsed(), outcome.stats.reads + outcome.stats.writes);
}

#[test]
#[ignore]
fn full_size_100k_by_100k() {
    full_size(Image::new(8, 100_001, 100_000), Job::Flip(Op::Hv), 100_000);
}

#[test]
#[ignore]
fn full_size_100k_wide_in_place() {
    full_size(Image::new(24, 100_000, 200), Job::InPlace(Op::Hv), 100_000);
    full_size(Image::new(24, 100_000, 201), Job::InPlace(Op::H), 100_000);
}

#[test]
#[ignore]
fn full_size_100k_wide_1_bit_in_place() {
    full_size(Image::new(1, 100_003, 4000).top_down(), Job::InPlace(Op::Hv), 4096);
}

#[test]
#[ignore]
fn full_size_conversion() {
    full_size(Image::new(24, 100_000, 10_000), Job::Convert(Format::Gray, Some(Op::V)), 100_000);
}
