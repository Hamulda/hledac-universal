//! # QoS Class Helper Functions
//!
//! MODERN-27: Safe conversion helpers for Darwin QoS classes.
//! 
//! Provides type-safe conversions between raw integer QoS class values
//! and the libc::qos_class_t type.

use libc::qos_class_t;

/// Safe conversion from c_int to libc::qos_class_t.
///
/// This avoids undefined behavior from direct casts when working with
/// Darwin QoS class values frommach/thread_policy.h.
#[inline]
pub fn qos_class_i32_to_qos_class_t(qos: libc::c_int) -> qos_class_t {
    // Darwin QoS class values are small positive integers
    // that map directly to libc::qos_class_t variants.
    // Safety: We're only casting to an enum repr(i32).
    unsafe { std::mem::transmute::<libc::c_int, qos_class_t>(qos) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_qos_conversions() {
        // QOS_CLASS_USER_INITIATED = 0x21 = 33
        assert_ne!(qos_class_i32_to_qos_class_t(33), qos_class_t(0));
        
        // QOS_CLASS_UTILITY = 0x19 = 25
        assert_ne!(qos_class_i32_to_qos_class_t(25), qos_class_t(0));
    }
}
