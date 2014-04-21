import unittest
from pypi2nix.datastructures import SpecSet, Spec, ConflictError


class TestSpecSet(unittest.TestCase):
    def test_adding_spec(self):
        """Adding a spec to a set."""
        specset = SpecSet()

        specset.add_spec('foo')
        specset.add_spec('foo')

        self.assertItemsEqual(
                list(specset),
                [Spec.from_line('foo')])

    def test_adding_multiple_specs(self):
        """Adding multiple specs to a set."""
        specset = SpecSet()

        specset.add_spec('django>=1.3')
        assert 'django>=1.3' in map(str, specset)

        specset.add_spec('django-pipeline')
        self.assertItemsEqual(['django>=1.3', 'django-pipeline'], map(str, specset))

        specset.add_spec('django<1.4')
        self.assertItemsEqual(['django>=1.3', 'django-pipeline', 'django<1.4'], map(str, specset))

    def test_explode(self):
        """Exploding a spec list into specs of max one predicate."""
        specset = SpecSet()

        specset.add_spec('django>=1.3,<1.4')
        specset.add_spec('django>=1.3.2,<1.5')

        self.assertItemsEqual(
                ['django>=1.3', 'django>=1.3.2', 'django<1.4', 'django<1.5'],
                map(str, specset.explode('django')))

    def test_normalizing_combines(self):
        """Normalizing combines predicates to a single Spec."""
        specset = SpecSet()

        specset.add_spec('django>=1.3')
        specset.add_spec('django<1.4')
        specset.add_spec('django>=1.3.2')
        specset.add_spec('django<1.3.99')

        normalized = specset.normalize()
        assert 'django>=1.3.2,<1.3.99' in map(str, normalized)

        specset.add_spec('django<=1.3.2')
        normalized = specset.normalize()

        assert 'django==1.3.2' in map(str, normalized)

    def test_normalizing_drops_obsoletes(self):
        """Normalizing combines predicates to a single Spec."""
        specset = SpecSet()

        specset.add_spec('django')
        specset.add_spec('django<1.4')

        normalized = specset.normalize()
        assert 'django<1.4' in map(str, normalized)
        assert 'django' not in map(str, normalized)

        specset = SpecSet()
        specset.add_spec('django>=1.4.1')
        specset.add_spec('django!=1.3.3')

        normalized = specset.normalize()
        assert 'django>=1.4.1' in map(str, normalized)
        assert 'django!=1.3.3' not in map(str, normalized)

    def test_normalizing_multiple_notequal_ops(self):
        """Normalizing multiple not-equal ops."""
        specset = SpecSet()
        specset.add_spec('django!=1.3')
        specset.add_spec('django!=1.4')

        normalized = specset.normalize()
        assert 'django!=1.3,!=1.4' in map(str, normalized)

    def test_normalizing_unequal_op(self):
        """Normalizing inequality and not-equal ops."""
        specset = SpecSet()
        specset.add_spec('django>=1.4.1')
        specset.add_spec('django!=1.4.1')

        normalized = specset.normalize()
        assert 'django>1.4.1' in map(str, normalized)

        specset = SpecSet()
        specset.add_spec('django<=1.4.1')
        specset.add_spec('django!=1.4.1')

        normalized = specset.normalize()
        assert 'django<1.4.1' in map(str, normalized)

        specset = SpecSet()
        specset.add_spec('django>=1.4.1')
        specset.add_spec('django!=1.4.2')

        normalized = specset.normalize()
        assert 'django>=1.4.1,!=1.4.2' in map(str, normalized)

        specset = SpecSet()
        specset.add_spec('django<=1.4.1')
        specset.add_spec('django>=1.4.1')
        specset.add_spec('django!=1.4.1')

        with self.assertRaises(ConflictError):
            specset.normalize()

    def test_normalizing_conflicts(self):
        """Normalizing can lead to conflicts."""
        specset = SpecSet()
        specset.add_spec('django==1.4.1')
        specset.add_spec('django!=1.4.1')

        with self.assertRaises(ConflictError):
            specset.normalize()

    def test_normalizing_keeps_source_info(self):
        """Normalizing keeps source information for specs."""
        specset = SpecSet()

        specset.add_spec(Spec.from_line('django', source='foo'))

        normalized = specset.normalize()
        assert 'foo' in [spec.source for spec in normalized]

        #specset.add_spec(Spec.from_line('django<1.4', source='bar'))
        #specset.add_spec(Spec.from_line('django<1.4', source='qux'))
        #specset.add_spec(Spec.from_line('django<1.4', source='mutt'))

        #normalized = specset.normalize()
        #assert 'foo' not in [spec.source for spec in normalized]
        #assert 'bar and mutt and qux' in [spec.source for spec in normalized]
