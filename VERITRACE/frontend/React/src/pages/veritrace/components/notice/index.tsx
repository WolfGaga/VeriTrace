import { motion } from 'framer-motion';

const Notice = () => {
    return (
        <motion.div
            className="text-center space-y-1 py-4"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.2 }}
        >
            <p className="text-body-sm font-medium text-text-primary">
                VeriTrace · Evolving mental models for deep research
            </p>
        </motion.div>
    );
};

export default Notice;
